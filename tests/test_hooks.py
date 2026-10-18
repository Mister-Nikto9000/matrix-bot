"""Крючки для приглашений (блок 5): превью закрытого, призыв после разбора, тексты."""
from datetime import date

from bot.content import messages as msg
from bot.handlers.matrix import full_cta, teaser_previews
from bot.services import texts
from bot.services.matrix_calc import calculate
from bot.services.referrals import friends_acc, unlock_plan
from tests.conftest import sent_texts

U = 1001
M = calculate(date(1994, 3, 7))  # C = 5, хвост 6-18-15


def test_friends_acc():
    assert [friends_acc(n) for n in (1, 2, 3, 5, 11, 21)] == [
        "1 друга", "2 друзей", "3 друзей", "5 друзей", "11 друзей", "21 друга",
    ]


def test_previews_for_new_user():
    lines = teaser_previews(M, unlock_plan(0, set()))
    assert lines == [
        f"🔒 Твоя денежная энергия: 5. {texts.name(5)} — откроется за 1 друга",
        "🔒 Кармический хвост: 6-18-? — откроется за 3 друзей",
    ]


def test_previews_follow_scale():
    # подарок «Деньги» уже открыт → вместо денег показываем отношения, пороги сдвигаются
    lines = teaser_previews(M, unlock_plan(0, {"money"}))
    assert lines[0].startswith(f"🔒 Твоя энергия отношений: {M.h}.") and lines[0].endswith("за 1 друга")
    assert lines[1] == "🔒 Кармический хвост: 6-18-? — откроется за 2 друзей"
    # всё открыто — превью нет
    assert teaser_previews(M, unlock_plan(10, set())) == []


def test_full_cta():
    assert full_cta(unlock_plan(0, set())) == "👉 Пригласи 1 друга — и сразу откроется сфера «Деньги»"
    assert full_cta(unlock_plan(1, {"money"})) == "👉 Пригласи 1 друга — и сразу откроется сфера «Отношения»"
    assert full_cta(unlock_plan(3, {"money", "relationships", "karma"})) == (
        "👉 Пригласи 2 друзей — и сразу откроются подробная совместимость и прогноз на год"
    )
    assert full_cta(unlock_plan(10, set())) == ""


async def test_teaser_and_full_contain_hooks(tg):
    await tg.send(U, "/start")
    teaser = sent_texts(await tg.send(U, "07.03.1994"))[1]
    assert teaser.endswith("🔒 Кармический хвост: 6-18-? — откроется за 3 друзей")
    calls = await tg.click(U, "full")
    assert sent_texts(calls)[2] == msg.SPHERES_TITLE + "\n\n👉 Пригласи 1 друга — и сразу откроется сфера «Деньги»"


def test_share_text_is_gender_neutral():
    assert msg.SHARE_REF_TEXT == (
        "Смотри, бот считает Матрицу Судьбы по дате рождения — мне попало очень точно. "
        "По моей ссылке тебе сразу откроют сферу «Деньги» 🎁"
    )
    for text in (msg.START_REF_GIFT, msg.CHANNELS_PROMPT, msg.BTN_SUBSCRIBED, msg.INVITER_ACTIVATED, msg.COMPAT_LINK):
        for word in ("Посчитал ", "подписался", "рассчитал ", " он "):
            assert word not in text
