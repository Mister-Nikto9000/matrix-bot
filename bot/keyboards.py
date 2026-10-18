from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.content import messages as msg
from bot.db.models import Channel
from bot.services.referrals import friends_acc

SPHERE_BY_ITEM = {item: key for key, item, _, _ in msg.SPHERE_BUTTONS}
SPHERE_TITLES = {key: f"{emoji} {title}" for key, _, emoji, title in msg.SPHERE_BUTTONS}


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _cb(text: str, data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data)]


def teaser_kb() -> InlineKeyboardMarkup:
    return _kb(_cb(msg.BTN_FULL, "full"), _cb(msg.BTN_COMPAT, "compat"), _cb(msg.BTN_INVITE, "invite"),
               _cb(msg.BTN_STORY, "story"))


def channels_kb(channels: list[Channel]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=msg.BTN_JOIN_REQUEST.format(title=ch.title) if ch.join_mode == "request" else ch.title, url=ch.url
        )]
        for ch in channels
    ]
    rows.append(_cb(msg.BTN_SUBSCRIBED, "check_subs"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def spheres_kb(unlocked: set[str], plan: dict[str, int | None]) -> InlineKeyboardMarkup:
    rows = []
    for key, item, emoji, title in msg.SPHERE_BUTTONS:
        if item in unlocked:
            text = f"{emoji} {title}"
        elif plan.get(item):
            text = msg.SPHERE_BUTTON_LOCKED.format(title=title, need=friends_acc(plan[item]))
        else:
            text = msg.SPHERE_BUTTON_LOCKED_SOON.format(title=title)
        rows.append(_cb(text, f"sphere:{key}"))
    rows.append(_cb(msg.BTN_INVITE, "invite"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def open_spheres_kb(items: list[str]) -> InlineKeyboardMarkup | None:
    rows = [
        _cb(msg.BTN_OPEN_SPHERE.format(title=SPHERE_TITLES[SPHERE_BY_ITEM[item]]), f"sphere:{SPHERE_BY_ITEM[item]}")
        for item in items
        if item in SPHERE_BY_ITEM
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def friend_kb(items: list[str], quest_done: bool = False) -> InlineKeyboardMarkup:
    """Под пушем «друг активировался»: открыть новые сферы и пригласить ещё (нажатия идут в push_log)."""
    rows = [
        _cb(msg.BTN_OPEN_SPHERE.format(title=SPHERE_TITLES[SPHERE_BY_ITEM[item]]), f"pc:friend:sphere_{SPHERE_BY_ITEM[item]}")
        for item in items
        if item in SPHERE_BY_ITEM
    ]
    if quest_done:
        rows.append(_cb(msg.BTN_OPEN_TOMORROW, "pc:friend:tomorrow"))
    rows.append(_cb(msg.BTN_INVITE_MORE, "pc:friend:invite"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_cb_kb(kind: str) -> InlineKeyboardMarkup:
    """kind: r — приглашение, c — совместимость. Нажатие пишет событие share_clicked."""
    return _kb(_cb(msg.BTN_SHARE, f"share:{kind}"))


def share_url_kb(link: str, text: str) -> InlineKeyboardMarkup:
    url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(text, safe='')}"
    return _kb([InlineKeyboardButton(text=msg.BTN_SEND_TO_FRIEND, url=url)])


def group_matrix_kb(link: str) -> InlineKeyboardMarkup:
    return _kb([InlineKeyboardButton(text=msg.BTN_GROUP_MATRIX, url=link)])


def compat_another_kb() -> InlineKeyboardMarkup:
    return _kb(_cb(msg.BTN_COMPAT_ANOTHER, "compat"))


def settings_kb(user=None) -> InlineKeyboardMarkup:
    rows = [_cb(msg.BTN_CHANGE_DATE, "change_date"), _cb(msg.BTN_CHANGE_GENDER, "change_gender")]
    if user is not None:
        rows.append(_cb(msg.BTN_DAILY_ON if user.notify_daily else msg.BTN_DAILY_OFF,
                        "daily:off" if user.notify_daily else "daily:on"))
        if user.notify_daily:
            rows.append(_cb(msg.BTN_DAILY_TIME.format(hour=user.notify_hour), "daily:time"))
        rows.append(_cb(msg.BTN_MARKETING_ON if user.notify_marketing else msg.BTN_MARKETING_OFF,
                        "mkt:off" if user.notify_marketing else "mkt:on"))
    return _kb(*rows)


def daily_offer_kb() -> InlineKeyboardMarkup:
    return _kb(_cb(msg.BTN_DAILY_YES, "daily:on"), _cb(msg.BTN_DAILY_NO, "daily:no"))


def daily_hours_kb() -> InlineKeyboardMarkup:
    return _kb([InlineKeyboardButton(text=f"{h}:00", callback_data=f"daily:h:{h}") for h in msg.DAILY_HOURS])


def push_kb(kind: str, buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Кнопки автопуша: (текст, действие) → pc:<вид>:<действие>."""
    return _kb(*[_cb(text, f"pc:{kind}:{action}") for text, action in buttons])


def daily_kb(tomorrow_open: bool, kind: str = "daily") -> InlineKeyboardMarkup:
    """Кнопки под числом дня; идут через pc:<вид>:<действие>, чтобы засчитывать нажатия."""
    return _kb(
        _cb(msg.BTN_TOMORROW_OPEN if tomorrow_open else msg.BTN_TOMORROW_LOCKED, f"pc:{kind}:tomorrow"),
        _cb(msg.BTN_INVITE_FRIEND, f"pc:{kind}:invite"),
    )


def gender_kb() -> InlineKeyboardMarkup:
    return _kb([
        InlineKeyboardButton(text=msg.BTN_GENDER_F, callback_data="gender:f"),
        InlineKeyboardButton(text=msg.BTN_GENDER_M, callback_data="gender:m"),
    ])


def delete_confirm_kb() -> InlineKeyboardMarkup:
    return _kb(_cb(msg.BTN_DELETE_YES, "delete_yes"), _cb(msg.BTN_DELETE_NO, "delete_no"))


def _row(*buttons: tuple[str, str]) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data) for text, data in buttons]


def channel_offer_kb(chat_id: int, connected: bool) -> InlineKeyboardMarkup:
    """Подключить канал (или сменить режим подключённого): chadd:<режим|skip>:<chat_id>."""
    return _kb(
        _row((msg.BTN_CH_PUBLIC, f"chadd:public:{chat_id}"), (msg.BTN_CH_REQUEST, f"chadd:request:{chat_id}")),
        _cb(msg.BTN_CH_KEEP if connected else msg.BTN_CH_SKIP, f"chadd:skip:{chat_id}"),
    )


def channel_manage_kb(ch: Channel) -> InlineKeyboardMarkup:
    """Кнопки под каналом в /channels: ch:<действие>:<id>."""
    first = [(msg.BTN_CH_ON if ch.is_active else msg.BTN_CH_OFF, f"ch:toggle:{ch.id}"),
             (msg.BTN_CH_MODE[ch.join_mode], f"ch:mode:{ch.id}")]
    rows = [_row(*first)]
    if ch.join_mode == "request":
        rows.append(_cb(msg.BTN_CH_AUTO[bool(ch.auto_approve)], f"ch:auto:{ch.id}"))
    rows.append(_row((msg.BTN_CH_UP, f"ch:up:{ch.id}"), (msg.BTN_CH_DOWN, f"ch:down:{ch.id}"),
                     (msg.BTN_CH_DELETE, f"ch:del:{ch.id}")))
    return _kb(*rows)


def channel_delete_kb(ch: Channel) -> InlineKeyboardMarkup:
    return _kb(_row((msg.BTN_CH_DELETE_YES, f"ch:delyes:{ch.id}"), (msg.BTN_CH_DELETE_NO, f"ch:delno:{ch.id}")))
