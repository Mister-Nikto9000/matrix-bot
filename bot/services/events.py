import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Event

log = logging.getLogger(__name__)

# start, birth_date_set, teaser_shown, channels_prompt, channels_ok, full_shown,
# invite_opened, share_clicked, compat_link_created, referral_started,
# referral_activated, unlock, sphere_opened


async def track(session: AsyncSession, user_id: int | None, name: str, payload: dict | None = None) -> None:
    session.add(Event(user_id=user_id, name=name, payload=payload))
    log.info("event %s user=%s %s", name, user_id, payload or "")
