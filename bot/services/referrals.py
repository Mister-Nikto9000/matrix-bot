"""Рефералка: привязка, активация, награды по шкале, антифрод."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import DAILY_ACTIVATION_LIMIT, GIFT_ITEM, REWARD_ORDER, REWARD_THRESHOLDS
from bot.db.models import Referral, Unlock, User
from bot.services import events, unlocks

log = logging.getLogger(__name__)


# --- Чистая логика шкалы ---

def reached_thresholds(count: int) -> int:
    return sum(1 for t in REWARD_THRESHOLDS if t <= count)


def rewards_due(count: int, unlocked: set[str], referral_granted: int) -> list[str]:
    """Какие пункты открыть сейчас.

    Каждый достигнутый порог открывает следующий ещё закрытый пункт REWARD_ORDER.
    referral_granted — сколько пунктов уже открыто за приглашения (подарок не считается).
    """
    due = reached_thresholds(count) - referral_granted
    if due <= 0:
        return []
    closed = [item for item in REWARD_ORDER if item not in unlocked]
    return closed[:due]


def unlock_plan(count: int, unlocked: set[str]) -> dict[str, int | None]:
    """Для каждого закрытого пункта — сколько ещё друзей нужно (None — порогов не осталось)."""
    remaining = REWARD_THRESHOLDS[reached_thresholds(count):]
    closed = [item for item in REWARD_ORDER if item not in unlocked]
    return {item: (remaining[i] - count if i < len(remaining) else None) for i, item in enumerate(closed)}


def plural(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        word = many
    elif n_abs % 10 == 1:
        word = one
    elif 2 <= n_abs % 10 <= 4:
        word = few
    else:
        word = many
    return f"{n} {word}"


def friends(n: int) -> str:
    return plural(n, "друг", "друга", "друзей")


def friends_acc(n: int) -> str:
    """Винительный падеж: «пригласи 1 друга / 3 друзей / 21 друга»."""
    return plural(n, "друга", "друзей", "друзей")


# --- Работа с БД ---

async def counted_count(session: AsyncSession, inviter_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Referral).where(Referral.inviter_id == inviter_id, Referral.counted.is_(True))
    ) or 0


async def _referral_granted(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Unlock).where(Unlock.user_id == user_id, Unlock.source == "referral")
    ) or 0


async def attach(session: AsyncSession, user: User, inviter: User, kind: str) -> bool:
    """Зафиксировать пригласившего (только один раз, не самого себя). kind: ref / comp."""
    if inviter.id == user.id or user.referrer_id is not None:
        return False
    user.referrer_id = inviter.id
    session.add(Referral(inviter_id=inviter.id, invited_id=user.id, kind=kind, counted=False))
    await session.flush()
    await events.track(session, user.id, "referral_started", {"inviter_id": inviter.id, "kind": kind})
    if kind == "ref":
        await unlocks.grant(session, user.id, GIFT_ITEM, "gift")
    return True


async def apply_rewards(session: AsyncSession, user: User) -> list[str]:
    """Открыть всё, что положено по шкале. Идемпотентно."""
    count = await counted_count(session, user.id)
    unlocked = await unlocks.get_unlocked(session, user.id)
    granted = await _referral_granted(session, user.id)
    new_items = rewards_due(count, unlocked, granted)
    for item in new_items:
        await unlocks.grant(session, user.id, item, "referral")
    return new_items


@dataclass
class Activation:
    inviter: User
    count: int
    new_items: list[str]


async def on_activated(session: AsyncSession, user: User) -> Activation | None:
    """Приглашённый впервые ввёл дату. Возвращает данные для уведомления пригласившего,
    если активация засчитана."""
    ref = await session.scalar(select(Referral).where(Referral.invited_id == user.id))
    if ref is None or ref.activated_at is not None:
        return None
    now = datetime.now(timezone.utc)
    recent = await session.scalar(
        select(func.count()).select_from(Referral).where(
            Referral.inviter_id == ref.inviter_id,
            Referral.counted.is_(True),
            Referral.activated_at >= now - timedelta(days=1),
        )
    ) or 0
    ref.activated_at = now
    ref.counted = recent < DAILY_ACTIVATION_LIMIT
    await session.flush()
    await events.track(session, user.id, "referral_activated", {"inviter_id": ref.inviter_id, "counted": ref.counted})
    if not ref.counted:
        log.warning("Antifraud: activation of user %s for inviter %s not counted (limit)", user.id, ref.inviter_id)
        return None

    inviter = await session.get(User, ref.inviter_id)
    if inviter is None:
        return None
    new_items = await apply_rewards(session, inviter)
    return Activation(inviter=inviter, count=await counted_count(session, inviter.id), new_items=new_items)
