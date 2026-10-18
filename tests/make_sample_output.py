"""Генерирует SAMPLE_OUTPUT.md — все сообщения бота для даты 07.03.1994.

Запуск из корня проекта: python -m tests.make_sample_output
Telegram не нужен: настоящий диспетчер + фейковый Bot API + временная SQLite.
"""
import asyncio
import os
import re
import tempfile
from pathlib import Path

os.environ["BOT_USERNAME"] = os.environ.get("SAMPLE_BOT_USERNAME", "numerologggg_bot")

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from bot.config import REWARD_ORDER  # noqa: E402
from bot.db.models import Base, Channel, Unlock, User  # noqa: E402
from bot.middlewares import RenderMiddleware  # noqa: E402
from tests.conftest import TG, FakeTelegram, _db_mw, _dp, buttons, sent  # noqa: E402

A, B, M = 1001, 1002, 1003
OUT = Path("SAMPLE_OUTPUT.md")


def to_md(html: str) -> str:
    text = re.sub(r"</?b>", "**", html)
    text = re.sub(r"</?i>", "_", text)
    text = re.sub(r"</?code>", "`", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def render(calls, chat_id: int) -> str:
    parts = []
    for m in sent(calls, chat_id):
        block = to_md(m.text)
        btns = buttons(m)
        if btns:
            block += "\n\nКнопки: " + " · ".join(
                f"`[{b.text}]`" + (f" → {b.url}" if b.url else "") for b in btns
            )
        parts.append(block)
    return "\n\n---\n\n".join(parts)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp, 's.db').as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        db = async_sessionmaker(engine, expire_on_commit=False)
        _db_mw.sessionmaker = db
        _dp.fsm.storage = MemoryStorage()
        fake = FakeTelegram()
        bot = Bot("123:test", session=fake, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        fake.middleware(RenderMiddleware())
        tg = TG(_dp, bot, fake)
        tg.auto_gender = None  # вопрос об обращении показываем и отвечаем явно

        sections: list[tuple[str, str]] = []

        def add(title, calls, chat_id=A):
            sections.append((title, render(calls, chat_id)))

        add("/start → приветствие и вопрос об обращении", await tg.send(A, "/start", first_name="Анна"))
        add("Кнопка «👩 Девушка» → просьба прислать дату", await tg.click(A, "gender:f", first_name="Анна"))
        add("Ввод даты 07.03.1994 → тизер (девушка)", await tg.send(A, "07.03.1994", first_name="Анна"))

        # Та же дата, обращение «Парень» (каналов ещё нет — полный разбор открывается сразу)
        await tg.send(M, "/start", first_name="Илья")
        add("Парень: кнопка «👨 Парень» → дата 07.03.1994 → тизер",
            await tg.click(M, "gender:m", first_name="Илья") + await tg.send(M, "07.03.1994", first_name="Илья"), M)
        add("Парень: «Полный разбор»", await tg.click(M, "full", first_name="Илья"), M)

        async with db() as s:
            s.add(Channel(chat_id=-100900, title="Матрица | Закрытый клуб", url="https://t.me/+AbCdEf123",
                          is_active=True, sort_order=1, joined_count=0, join_mode="request", auto_approve=False))
            await s.commit()
        add("«Полный разбор» → экран подписки на канал с заявкой", await tg.click(A, "full", first_name="Анна"))
        await tg.join_request(A, -100900)
        add("Заявка подана → «Проверить подписку» → полный разбор (девушка) и меню сфер",
            await tg.click(A, "check_subs", first_name="Анна"))

        add("Экран приглашения (/invite)", await tg.send(A, "/invite", first_name="Анна"))
        add("Кнопка «Поделиться»", await tg.click(A, "share:r", first_name="Анна"))

        # Для примера открываем все сферы
        async with db() as s:
            user = await s.scalar(select(User).where(User.tg_id == A))
            for item in REWARD_ORDER:
                s.add(Unlock(user_id=user.id, item=item, source="admin"))
            await s.commit()
        for key, title in [("money", "Деньги"), ("relationships", "Отношения"), ("karma", "Кармический хвост"),
                           ("year", "Прогноз на год"), ("practices", "Практики"), ("ai", "AI-консультант")]:
            add(f"Сфера «{title}»", await tg.click(A, f"sphere:{key}", first_name="Анна"))

        add("Совместимость (/compat) — ссылка для партнёра", await tg.send(A, "/compat", first_name="Анна"))
        async with db() as s:
            code = (await s.scalar(select(User).where(User.tg_id == A))).ref_code
        add("Партнёр открывает ссылку c_… → вопрос об обращении", await tg.send(B, f"/start c_{code}", first_name="Максим"), B)
        add("Партнёр выбирает «👨 Парень»", await tg.click(B, "gender:m", first_name="Максим"), B)
        calls = await tg.send(B, "15.08.1990", first_name="Максим")
        add("Партнёр вводит 15.08.1990 — что видит партнёр (кратко, без подробностей)", calls, B)
        add("…и что приходит Анне (подробная совместимость открыта)", calls, A)

        await engine.dispose()

    lines = [
        "# Пример выдачи бота",
        "",
        "Дата рождения: **07.03.1994**, имя пользователя в Telegram — Анна (обращение «Девушка»). "
        "Для сравнения тизер и полный разбор той же даты показаны для обращения «Парень» (Илья). "
        "Совместимость — с партнёром 15.08.1990 (Максим).",
        "",
        "Сгенерировано командой `python -m tests.make_sample_output` через настоящий диспетчер бота "
        "(Telegram и БД подменены). HTML-разметка переведена в Markdown, кнопки перечислены под сообщением. "
        "Для примера в разделе сфер у Анны открыты все награды; прогноз на год — для текущего года.",
        "",
    ]
    for title, body in sections:
        lines += [f"## {title}", "", body or "_(нет сообщений)_", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written {OUT} ({len(sections)} sections)")


if __name__ == "__main__":
    asyncio.run(main())
