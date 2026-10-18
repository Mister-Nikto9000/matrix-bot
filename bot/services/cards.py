"""Карточка-картинка для сторис: номер и название аркана, одна фраза и ссылка на бота. Даты рождения нет.

Шрифт DejaVu Sans лежит в проекте (bot/assets/fonts) — в нём есть кириллица, системные шрифты не нужны.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_REGULAR = ASSETS / "DejaVuSans.ttf"
FONT_BOLD = ASSETS / "DejaVuSans-Bold.ttf"

SIZE = (1080, 1920)  # формат сторис 9:16
MARGIN = 90
TOP_COLOR, BOTTOM_COLOR = (38, 20, 84), (96, 52, 140)
GOLD, WHITE, SOFT = (255, 214, 120), (255, 255, 255), (214, 200, 240)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _gradient(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP_COLOR, BOTTOM_COLOR)))
    return img


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Перенос по словам так, чтобы каждая строка влезала в max_width."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _fits(draw, lines: list[str], font, width: int, max_lines: int) -> bool:
    return len(lines) <= max_lines and all(draw.textlength(line, font=font) <= width for line in lines)


def _draw_block(draw, lines, font, y, fill, size, line_gap=1.25) -> int:
    """Рисует строки по центру, возвращает y под блоком. Если строка не влезает в поля — ValueError."""
    width = size[0]
    for line in lines:
        w = draw.textlength(line, font=font)
        if w > width - 2 * MARGIN:
            raise ValueError(f"Текст не помещается на карточку: {line!r}")
        draw.text(((width - w) / 2, y), line, font=font, fill=fill)
        y += int(font.size * line_gap)
    return y


def render_card(number: int, name: str, phrase: str, bot_username: str) -> bytes:
    """PNG 1080×1920. Возвращает байты файла."""
    img = _gradient(SIZE)
    draw = ImageDraw.Draw(img)
    width, height = SIZE
    safe = width - 2 * MARGIN

    # декоративные кольца
    for r, alpha in ((820, 40), (640, 60), (460, 90)):
        cx, cy = width // 2, 760
        draw.ellipse((cx - r // 2 - 40, cy - r // 2 - 40, cx + r // 2 + 40, cy + r // 2 + 40),
                     outline=tuple(min(255, c + alpha) for c in TOP_COLOR), width=3)

    y = _draw_block(draw, ["МАТРИЦА СУДЬБЫ"], _font(FONT_BOLD, 44), 150, SOFT, SIZE)
    y = _draw_block(draw, [str(number)], _font(FONT_BOLD, 420), 300, GOLD, SIZE, 1.0)

    # название: уменьшаем шрифт, пока не поместится в две строки
    for size in (110, 96, 84, 72):
        font = _font(FONT_BOLD, size)
        lines = wrap(draw, name, font, safe)
        if _fits(draw, lines, font, safe, 2):
            break
    y = _draw_block(draw, lines, font, 820, WHITE, SIZE)

    draw.line([(width // 2 - 90, y + 30), (width // 2 + 90, y + 30)], fill=GOLD, width=5)

    for size in (58, 52, 46, 40):
        font = _font(FONT_REGULAR, size)
        lines = wrap(draw, phrase, font, safe)
        if _fits(draw, lines, font, safe, 7):
            break
    _draw_block(draw, lines, font, y + 90, WHITE, SIZE, 1.35)

    _draw_block(draw, ["Рассчитай свою матрицу — бесплатно"], _font(FONT_REGULAR, 44), height - 330, SOFT, SIZE)
    _draw_block(draw, [f"t.me/{bot_username}"], _font(FONT_BOLD, 62), height - 250, GOLD, SIZE)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
