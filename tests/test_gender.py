"""Выбор обращения (пол) и тексты по роду {м|ж}."""
import json
import re
import string

import pytest
from sqlalchemy import select, update

from bot.content import messages as msg
from bot.db.models import Event, Unlock, User
from bot.services import texts
from bot.services.gender import has_templates, render
from tests.conftest import buttons, sent, sent_texts

U, A, B = 4001, 4002, 4003


async def _user(db, tg_id) -> User:
    async with db() as s:
        return await s.scalar(select(User).where(User.tg_id == tg_id))


async def _forget_gender(db, tg_id):
    """Как у пользователей, пришедших до появления выбора пола."""
    async with db() as s:
        await s.execute(update(User).where(User.tg_id == tg_id).values(gender=None))
        await s.commit()


# --- render ---

def test_render():
    s = "Ты {энергичный|энергичная}, {name} рассчитал{|а} матрицу"
    assert render(s, "m") == "Ты энергичный, {name} рассчитал матрицу"
    assert render(s, "f") == "Ты энергичная, {name} рассчитала матрицу"
    assert render(s, None) == render(s, "f")  # по умолчанию — женская форма
    assert render("{он|она}", "m") == "он"


def _all_arcana_texts():
    data = json.loads(texts.ARCANA_PATH.read_text(encoding="utf-8"))
    for n, fields in data.items():
        for field, value in fields.items():
            for i, s in enumerate(value if isinstance(value, list) else [value]):
                yield f"arcana[{n}].{field}[{i}]", s


def _all_message_texts():
    for name, value in vars(msg).items():
        if name.startswith("_") or not name.isupper():
            continue
        items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else [("", value)]
        for key, v in items:
            for j, s in enumerate(v if isinstance(v, (tuple, list)) else [v]):
                if isinstance(s, str):
                    yield f"messages.{name}[{key}][{j}]", s


def _fill_format_fields(s: str) -> str:
    """Подставляет заглушки в {name}-плейсхолдеры str.format."""
    fields = {f for _, f, _, _ in string.Formatter().parse(s) if f}
    return s.format(**{f: "X" for f in fields})


@pytest.mark.parametrize("gender", ["f", "m"])
def test_all_texts_render_clean(gender):
    checked = 0
    for where, s in [*_all_arcana_texts(), *_all_message_texts()]:
        out = render(s, gender)
        if where.startswith("messages."):
            out = _fill_format_fields(out)  # не должно падать: шаблоны {м|ж} уже раскрыты
        assert "{" not in out and "}" not in out, f"{where}: {out!r}"
        assert out.strip(), f"{where}: пустая строка"
        assert "  " not in out, f"{where}: двойной пробел {out!r}"
        checked += 1
    assert checked > 250


def test_templates_are_where_expected():
    arcana = dict(_all_arcana_texts())
    assert render(arcana["arcana[7].personality[0]"], "f").startswith("Ты энергичная, целеустремлённая натура, которой")
    assert render(arcana["arcana[7].personality[0]"], "m").startswith("Ты энергичный, целеустремлённый человек, которому")
    assert has_templates(msg.SOON) and has_templates(msg.COMPAT_PARTNER_NO_DATE)


# --- сценарий ---

async def test_new_user_flow(tg, db):
    tg.auto_gender = None
    calls = await tg.send(U, "/start")
    assert sent_texts(calls) == [msg.START_HELLO, msg.GENDER_ASK]
    assert [b.callback_data for b in buttons(sent(calls)[1])] == ["gender:f", "gender:m"]
    assert [b.text for b in buttons(sent(calls)[1])] == ["👩 Девушка", "👨 Парень"]

    # текст вместо кнопки — вопрос повторяется
    calls = await tg.send(U, "привет")
    assert sent_texts(calls) == [msg.GENDER_ASK] and buttons(sent(calls)[0])

    calls = await tg.click(U, "gender:m")
    assert sent_texts(calls) == [msg.ASK_DATE]
    assert (await _user(db, U)).gender == "m"

    await tg.send(U, "07.03.1994")
    calls = await tg.click(U, "full")
    full = sent_texts(calls)[0]
    assert "Ты энергичный, целеустремлённый человек, которому" in full
    assert "{" not in full and "|" not in full

    async with db() as s:
        ev = await s.scalar(select(Event).where(Event.name == "gender_set"))
        assert ev.payload == {"gender": "m"}


async def test_female_reading(tg, db):
    await tg.send(U, "/start")  # тест-клиент отвечает «Девушка»
    await tg.send(U, "07.03.1994")
    assert (await _user(db, U)).gender == "f"
    full = sent_texts(await tg.click(U, "full"))[0]
    assert "Ты энергичная, целеустремлённая натура, которой" in full


async def test_date_sent_instead_of_answer_is_kept(tg, db):
    tg.auto_gender = None
    await tg.send(U, "/start")
    assert sent_texts(await tg.send(U, "07.03.1994")) == [msg.GENDER_ASK]
    calls = await tg.click(U, "gender:f")
    assert "07.03.1994" in sent_texts(calls)[0] and "Матрица Судьбы" in sent_texts(calls)[1]


async def test_ref_gift_then_question(tg, db):
    await tg.send(A, "/start")
    await tg.send(A, "07.03.1994")
    code = (await _user(db, A)).ref_code
    tg.auto_gender = None
    calls = await tg.send(B, f"/start r_{code}")
    assert sent_texts(calls) == [msg.START_HELLO + msg.START_REF_GIFT, msg.GENDER_ASK]


async def test_old_user_asked_before_reading(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    await _forget_gender(db, U)
    tg.auto_gender = None

    for action in (lambda: tg.send(U, "/matrix"), lambda: tg.send(U, "/start"), lambda: tg.click(U, "full")):
        assert sent_texts(await action()) == [msg.GENDER_ASK]

    # ответ — и сразу показываем то, что просили (последнее: полный разбор)
    calls = await tg.click(U, "gender:m")
    assert "полный разбор" in sent_texts(calls)[0] and "энергичный" in sent_texts(calls)[0]


async def test_old_user_teaser_and_sphere(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    await _forget_gender(db, U)
    tg.auto_gender = None
    assert sent_texts(await tg.send(U, "/matrix")) == [msg.GENDER_ASK]
    assert "вот твоя Матрица Судьбы" in sent_texts(await tg.click(U, "gender:f"))[0]

    await _forget_gender(db, U)
    async with db() as s:
        user = await s.scalar(select(User).where(User.tg_id == U))
        s.add(Unlock(user_id=user.id, item="ai", source="admin"))
        await s.commit()
    assert sent_texts(await tg.click(U, "sphere:ai")) == [msg.GENDER_ASK]
    calls = await tg.click(U, "gender:m")
    assert sent_texts(calls) == ["🌱 Скоро появится — ты получишь доступ первым."]


async def test_old_user_compat_link(tg, db):
    await tg.send(A, "/start", first_name="Аня")
    await tg.send(A, "07.03.1994", first_name="Аня")
    await tg.send(B, "/start", first_name="Борис")
    await tg.send(B, "23.11.1985", first_name="Борис")
    await _forget_gender(db, B)
    code = (await _user(db, A)).ref_code
    tg.auto_gender = None
    assert sent_texts(await tg.send(B, f"/start c_{code}", first_name="Борис")) == [msg.GENDER_ASK]
    calls = await tg.click(B, "gender:m", first_name="Борис")
    assert "Совместимость: ты и Аня" in sent_texts(calls, B)[0]
    assert "Совместимость: ты и Борис" in sent_texts(calls, A)[0]


async def test_other_person_gender(tg, db):
    # «указал{|а}» — по полу пригласившего, а не читающего
    await tg.send(A, "/start", first_name="Олег")
    tg.auto_gender = "m"
    await tg.click(A, "gender:m", first_name="Олег")
    code = (await _user(db, A)).ref_code
    tg.auto_gender = "f"
    await tg.send(B, f"/start c_{code}", first_name="Аня")
    calls = await tg.send(B, "23.11.1985", first_name="Аня")
    assert "Олег ещё не указал дату рождения" in sent_texts(calls, B)[-1]


async def test_settings_change_gender_and_deletedata(tg, db):
    await tg.send(U, "/start")
    await tg.send(U, "07.03.1994")
    calls = await tg.send(U, "/settings")
    assert [b.callback_data for b in buttons(sent(calls)[0])][:2] == ["change_date", "change_gender"]
    assert buttons(sent(calls)[0])[1].text == msg.BTN_CHANGE_GENDER

    tg.auto_gender = None
    assert sent_texts(await tg.click(U, "change_gender")) == [msg.GENDER_ASK]
    assert sent_texts(await tg.click(U, "gender:m")) == [msg.GENDER_SAVED]
    assert (await _user(db, U)).gender == "m"

    await tg.send(U, "/deletedata")
    await tg.click(U, "delete_yes")
    assert (await _user(db, U)).gender is None


async def test_render_applies_to_buttons_and_alerts(tg, db):
    calls = await tg.send(U, "/start")
    for m in sent(calls):
        assert not has_templates(m.text)
        assert not any(has_templates(b.text) for b in buttons(m))
    assert not re.search(r"\{[^}]*\|", "".join(sent_texts(calls)))


def test_check_script_finds_nothing():
    from scripts.check_gender import scan
    assert scan() == []
