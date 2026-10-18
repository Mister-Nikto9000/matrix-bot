import re
from datetime import date

from bot.config import MIN_BIRTH_YEAR

_DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{4})\s*$")


def parse_birth_date(text: str, today: date | None = None) -> date | None:
    """ДД.ММ.ГГГГ → date. None, если дата некорректна, в будущем или год вне 1920–текущий."""
    today = today or date.today()
    m = _DATE_RE.match(text or "")
    if not m:
        return None
    day, month, year = (int(x) for x in m.groups())
    if not MIN_BIRTH_YEAR <= year <= today.year:
        return None
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if d > today:
        return None
    return d


def format_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")
