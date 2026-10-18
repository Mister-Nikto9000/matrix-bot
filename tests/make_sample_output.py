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

from aiogram.methods import AnswerInlineQuery, EditMessageText, SendMessage  # noqa: E402

from bot.config import REWARD_ORDER  # noqa: E402
from bot.db.models import Base, Channel, Unlock, User  # noqa: E402
from bot.middlewares import RenderMiddleware  # noqa: E402
from bot.handlers.admin_stats import render_report  # noqa: E402
from bot.services import daily  # noqa: E402
from tests.conftest import TG, FakeTelegram, _db_mw, _dp, buttons, sent  # noqa: E402

A, B, M, ADMIN = 1001, 1002, 1003, 999
OUT = Path("SAMPLE_OUTPUT.md")


def to_md(html: str) -> str:
    text = re.sub(r"</?b>", "**", html)
    text = re.sub(r"</?i>", "_", text)
    text = re.sub(r"</?code>", "`", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def render(calls, chat_id: int) -> str:
    parts = []
    # сообщения бота и правки сообщений (выбор времени числа дня и т. п. меняет текст на месте)
    shown = [c for c in calls if isinstance(c, (SendMessage, EditMessageText)) and c.chat_id == chat_id]
    for m in shown:
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
        add("Заявка подана → «Проверить подписку» → полный разбор (девушка): два сообщения, меню сфер, предложение числа дня",
            await tg.click(A, "check_subs", first_name="Анна"))

        # Число дня: подписка и утренняя рассылка (09:00 МСК сегодняшнего дня)
        add("«☀️ Присылать число дня» → выбор времени", await tg.click(A, "daily:on", first_name="Анна"))
        add("Выбор «9:00»", await tg.click(A, "daily:h:9", first_name="Анна"))
        from datetime import datetime, timezone
        from bot.services.clock import day_start_utc, msk_today
        nine_msk = day_start_utc(msk_today()).replace(tzinfo=timezone.utc) + __import__("datetime").timedelta(hours=9)
        start = len(fake.calls)
        await daily.run_daily(bot, db, nine_msk)
        add("Утренний пуш «Число дня» в 9:00 (квест дня внутри)", fake.calls[start:])

        add("Экран приглашения (/invite) с квестом дня", await tg.send(A, "/invite", first_name="Анна"))
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
        add("…и что приходит Анне (подробная совместимость + пуш «друг активировался» с выполненным квестом)", calls, A)
        add("Кнопка «Открыть прогноз на завтра» из пуша", await tg.click(A, "pc:friend:tomorrow", first_name="Анна"))

        # Inline-режим и картинка для сторис
        await tg.inline(A, first_name="Анна")
        answer = [c for c in fake.calls if isinstance(c, AnswerInlineQuery)][-1]
        inline_md = []
        for r in answer.results:
            btn = r.reply_markup.inline_keyboard[0][0]
            inline_md.append(f"**{r.title}** — _{r.description}_\n" + to_md(r.input_message_content.message_text)
                             + f"\n\nКнопка: `[{btn.text}]` → {btn.url}")
        sections.append(("Inline-режим: @бот в любом чате → три карточки", "\n\n---\n\n".join(inline_md)))
        await tg.click(A, "story", first_name="Анна")
        story = [c for c in fake.calls if getattr(c, "caption", None)][-1]
        sections.append(("Кнопка «🖼 Картинка для сторис» — подпись к PNG 1080×1920 (на картинке номер, название, фраза, ссылка)",
                         to_md(story.caption)))

        # Админ: метрики
        await tg.send(ADMIN, "/start", first_name="Админ")
        async with db() as s:
            for section, period in (("summary", "all"), ("funnel", "all"), ("virality", "all"), ("sources", "all"),
                                    ("mailings", "all")):
                text, _ = await render_report(s, section, period)
                sections.append((f"/stats → {section}", to_md(text)))
        add("/link vk_may", await tg.send(ADMIN, "/link vk_may"), ADMIN)

        await engine.dispose()

    lines = [
        "# Пример выдачи бота",
        "",
        "Версия 2: полный разбор в 2–3 раза длиннее и выходит двумя сообщениями, число дня, квест дня, пуши, "
        "inline-режим, картинка для сторис, практики, метрики для админа.\n\n"
        "Дата рождения: **07.03.1994**, имя пользователя в Telegram — Анна (обращение «Девушка»). "
        "Для сравнения тизер и полный разбор той же даты показаны для обращения «Парень» (Илья). "
        "Совместимость — с партнёром 15.08.1990 (Максим).",
        "",
        "Сгенерировано командой `python -m tests.make_sample_output` через настоящий диспетчер бота "
        "(Telegram и БД подменены). HTML-разметка переведена в Markdown, кнопки перечислены под сообщением. "
        "Для примера в разделе сфер у Анны открыты все награды; прогноз на год и число дня — для даты генерации файла.",
        "",
    ]
    for title, body in sections:
        lines += [f"## {title}", "", body or "_(нет сообщений)_", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written {OUT} ({len(sections)} sections)")


if __name__ == "__main__":
    asyncio.run(main())
