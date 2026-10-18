from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.config import REWARD_ORDER, get_settings
from bot.content import messages as msg
from bot.db.models import User
from bot.services import events, referrals, unlocks, users
from bot.services.referrals import Activation, friends

router = Router(name="invite")


def ref_link(user: User) -> str:
    return f"https://t.me/{get_settings().bot_username}?start=r_{user.ref_code}"


def compat_link(user: User) -> str:
    return f"https://t.me/{get_settings().bot_username}?start=c_{user.ref_code}"


def render_invite(user: User, count: int, unlocked: set[str]) -> str:
    plan = referrals.unlock_plan(count, unlocked)
    rewards = []
    for item in REWARD_ORDER:
        title = msg.ITEM_TITLES[item]
        if item in unlocked:
            rewards.append(msg.REWARD_OPEN.format(title=title))
        elif plan.get(item):
            rewards.append(msg.REWARD_LOCKED.format(title=title, need=friends(plan[item])))
        else:
            rewards.append(msg.REWARD_LOCKED_SOON.format(title=title))

    reachable = [(item, need) for item, need in plan.items() if need]
    if reachable:
        item, need = reachable[0]
        next_line = msg.INVITE_NEXT.format(item=msg.ITEM_GENITIVE[item], need=friends(need))
    elif not plan:
        next_line = msg.INVITE_ALL_OPEN
    else:
        next_line = ""
    return msg.INVITE.format(link=ref_link(user), count=count, next_line=next_line, rewards="\n".join(rewards))


async def send_invite(message: Message, session: AsyncSession, user: User) -> None:
    count = await referrals.counted_count(session, user.id)
    unlocked = await unlocks.get_unlocked(session, user.id)
    await message.answer(
        render_invite(user, count, unlocked), reply_markup=kb.share_cb_kb("r"), disable_web_page_preview=True
    )
    await events.track(session, user.id, "invite_opened")


async def notify_inviter(bot: Bot, activation: Activation) -> None:
    text = msg.INVITER_ACTIVATED.format(count=activation.count)
    if activation.new_items:
        text += msg.INVITER_UNLOCKED.format(items=", ".join(msg.ITEM_TITLES[i] for i in activation.new_items))
    await users.safe_send(bot, activation.inviter, text, reply_markup=kb.open_spheres_kb(activation.new_items))


@router.message(Command("invite"))
async def cmd_invite(message: Message, session: AsyncSession, user: User) -> None:
    await send_invite(message, session, user)


@router.callback_query(F.data == "invite")
async def cb_invite(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    await send_invite(call.message, session, user)


@router.callback_query(F.data.in_({"share:r", "share:c"}))
async def cb_share(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    kind = call.data.split(":", 1)[1]
    if kind == "r":
        link, text = ref_link(user), msg.SHARE_REF_TEXT
    else:
        link, text = compat_link(user), msg.SHARE_COMPAT_TEXT
    await call.answer()
    await call.message.answer(
        msg.SHARE_READY.format(text=text, link=link),
        reply_markup=kb.share_url_kb(link, text),
        disable_web_page_preview=True,
    )
    await events.track(session, user.id, "share_clicked", {"kind": "ref" if kind == "r" else "comp"})
