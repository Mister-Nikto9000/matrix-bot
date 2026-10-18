"""Отправка длинных текстов: Telegram принимает до 4096 символов, режем с запасом по абзацам."""
import re

from aiogram.types import InlineKeyboardMarkup, Message

TELEGRAM_LIMIT = 4096
CHUNK_LIMIT = 3800

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _fit(paragraph: str, limit: int) -> list[str]:
    """Абзац длиннее лимита: сначала по строкам, затем по предложениям, в крайнем случае по символам."""
    if len(paragraph) <= limit:
        return [paragraph]
    pieces: list[str] = []
    for line in paragraph.split("\n"):
        if len(line) <= limit:
            pieces.append(line)
            continue
        for sentence in _SENTENCE_END.split(line):
            pieces.extend([sentence] if len(sentence) <= limit else _hard_split(sentence, limit))
    parts, cur = [], ""
    for piece in pieces:
        if cur and len(cur) + 1 + len(piece) > limit:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur}\n{piece}" if cur else piece
    if cur:
        parts.append(cur)
    return parts


def split_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Режет текст на части не длиннее limit, предпочитая границы абзацев (пустая строка)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur = ""
    for paragraph in text.split("\n\n"):
        for part in _fit(paragraph, limit):
            if cur and len(cur) + 2 + len(part) > limit:
                chunks.append(cur)
                cur = part
            else:
                cur = f"{cur}\n\n{part}" if cur else part
    if cur:
        chunks.append(cur)
    return chunks


async def send_long(
    message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None, **kwargs
) -> Message:
    """Отправляет текст одним или несколькими сообщениями; кнопки — только под последним."""
    chunks = split_text(text)
    sent = None
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        sent = await message.answer(chunk, reply_markup=reply_markup if last else None, **kwargs)
    return sent
