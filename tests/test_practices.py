"""Блок 8: сфера «Практики» — упражнение на неделю для предназначения и зоны комфорта."""
import re
from datetime import date

from sqlalchemy import select

from bot.content import messages as msg
from bot.db.models import Unlock, User
from bot.handlers.matrix import render_sphere
from bot.services import texts
from bot.services.gender import has_templates, render
from bot.services.matrix_calc import calculate
from tests.conftest import sent_texts

U = 9601


def test_every_arcana_has_a_practice_with_steps():
    for n in range(1, 23):
        for gender in ("f", "m"):
            t = render(texts.text(n, "practice"), gender)
            body = re.sub(r"«[^»]*»", "", t)  # название практики в кавычках не считаем за предложение
            sentences = re.findall(r"[.!?](?:\s|$)", body)
            assert 4 <= len(sentences) <= 5, (n, len(sentences))
            assert 350 <= len(t) <= 800 and not has_templates(t), (n, len(t))
            assert t.startswith("Практика «"), n
            assert re.search(r"понедельник|вторник|сред|четверг|пятниц|воскресень|выходн|каждый|каждое|кажд", t), n


def test_practices_use_purpose_and_comfort_arcana():
    m = calculate(date(1985, 11, 23))
    assert m.purpose != m.e
    text = render_sphere("practices", m)
    assert f"Для предназначения — {m.purpose}. {texts.name(m.purpose)}" in text
    assert f"Для зоны комфорта — {m.e}. {texts.name(m.e)}" in text
    assert texts.text(m.purpose, "practice") in text and texts.text(m.e, "practice") in text


def test_single_block_when_purpose_equals_comfort():
    m = calculate(date(1994, 3, 7))  # предназначение и зона комфорта — 3. Императрица
    assert m.purpose == m.e == 3
    text = render_sphere("practices", m)
    assert text.count("Практика «") == 1 and "Для предназначения и зоны комфорта — 3. Императрица" in text


def test_ai_sphere_is_still_a_stub():
    assert render_sphere("ai", calculate(date(1994, 3, 7))) == msg.SOON


async def test_practices_sphere_in_bot_after_unlock(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "23.11.1985")
    calls = await tg.click(U, "sphere:practices")  # закрыта
    assert sent_texts(calls) == []
    async with db() as s:
        user = await s.scalar(select(User).where(User.tg_id == U))
        s.add(Unlock(user_id=user.id, item="practices", source="admin"))
        await s.commit()
    text = sent_texts(await tg.click(U, "sphere:practices"))[0]
    assert "Практики на неделю" in text and "Практика «" in text and "не заменяют помощь специалиста" in text
    assert not has_templates(text)
