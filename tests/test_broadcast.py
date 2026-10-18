"""Блок 6: /broadcast — предпросмотр, сегменты, тест, запуск с подтверждением, отчёт."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, update

from bot.content import messages as msg
from bot.db.models import Broadcast, Referral, User
from bot.handlers.admin_stats import render_mailings
from bot.services import broadcast as bc_svc
from bot.services import pushes
from bot.services.stats import make_period
from tests.conftest import alerts, buttons, sent, sent_texts

ADMIN = 999
G1, G2, B1, N1, S1 = 9101, 9102, 9103, 9104, 9105  # девушка×2, парень, ещё без даты, с отключёнными рассылками


async def _wait_broadcasts():
    pending = [t for t in bc_svc.tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending)


async def _setup_audience(tg, db):
    tg.auto_gender = "f"
    for uid in (G1, G2):
        await tg.send(uid, "/start")
        await tg.send(uid, "07.03.1994")
    tg.auto_gender = "m"
    await tg.send(B1, "/start")
    await tg.send(B1, "23.11.1985")
    await tg.send(N1, "/start")  # не активирован — в сегменты не входит
    tg.auto_gender = "f"
    await tg.send(S1, "/start")
    await tg.send(S1, "01.01.2001")
    await tg.send(S1, "/stop")
    tg.auto_gender = "f"


def _segment_buttons(call):
    return {b.callback_data: b.text for b in buttons(call)}


async def _start_flow(tg, text="Привет, <b>друзья</b>! Ты {рад|рада}?"):
    await tg.send(ADMIN, "/broadcast")
    return await tg.send(ADMIN, text)


async def test_non_admin_cannot_broadcast(tg):
    assert await tg.send(G1, "/broadcast") == []


async def test_preview_and_segments_with_counts(tg, db):
    await _setup_audience(tg, db)
    assert sent_texts(await tg.send(ADMIN, "/broadcast")) == [msg.BC_ASK_TEXT]
    calls = await tg.send(ADMIN, "Привет, <b>друзья</b>! Ты {рад|рада}?")
    texts = sent_texts(calls)
    assert texts[0].startswith("Привет, <b>друзья</b>") and "Ты рада?" in texts[0]  # предпросмотр как увидит получатель
    assert texts[1] == msg.BC_PREVIEW_HEADER
    segs = _segment_buttons(sent(calls)[1])
    # активированных 4 (две девушки, парень, S1), но S1 отключил рассылки — в сегментах 3
    assert segs["bc:seg:all"] == "Все активированные (3)"
    assert segs["bc:seg:girls"] == "Девушки (2)" and segs["bc:seg:boys"] == "Парни (1)"
    assert segs["bc:seg:no_channels"] == "Не прошли каналы (3)"
    assert segs["bc:seg:no_invites"] == "Без приглашённых (3)"
    assert "bc:cancel" in segs


async def test_segments_no_channels_and_no_invites(tg, db):
    await _setup_audience(tg, db)
    async with db() as s:
        g1 = await s.scalar(select(User).where(User.tg_id == G1))
        g2 = await s.scalar(select(User).where(User.tg_id == G2))
        g1.channels_ok_at = datetime.now(timezone.utc)
        s.add(Referral(inviter_id=g2.id, invited_id=g1.id, kind="ref", counted=True))
        await s.commit()
        assert await bc_svc.segment_size(s, "no_channels") == 2
        assert await bc_svc.segment_size(s, "no_invites") == 2  # g2 уже кого-то пригласил


async def test_bad_html_and_too_long_text(tg):
    await tg.send(ADMIN, "/broadcast")
    assert "Telegram не принял разметку" in sent_texts(await tg.send(ADMIN, "BADHTML <b>"))[-1]
    assert sent_texts(await tg.send(ADMIN, "я" * 5000)) == [msg.BC_TOO_LONG]


async def test_full_flow_sends_only_to_segment_and_reports(tg, db):
    await _setup_audience(tg, db)
    await _start_flow(tg)
    calls = await tg.click(ADMIN, "bc:seg:girls")
    summary = sent(calls)[0]
    assert "Девушки" in summary.text and "<b>2</b>" in summary.text
    assert [b.callback_data for b in buttons(summary)] == ["bc:test", "bc:go", "bc:back", "bc:cancel"]

    # тест — только админу, в его форме рода
    calls = await tg.click(ADMIN, "bc:test")
    assert [m.chat_id for m in sent(calls)] == [ADMIN] and "Ты рада?" in sent_texts(calls)[0]

    # запуск требует подтверждения
    calls = await tg.click(ADMIN, "bc:go")
    assert "Точно запустить" in sent_texts(calls)[0] and "bc:yes" in [b.callback_data for b in buttons(sent(calls)[0])]

    start = len(tg.fake.calls)
    await tg.click(ADMIN, "bc:yes")
    await _wait_broadcasts()
    delivered = sent(tg.fake.calls[start:])
    by_chat = {m.chat_id: m.text for m in delivered if m.chat_id != ADMIN}
    assert set(by_chat) == {G1, G2}  # парню, не активированному и отключившему рассылки — ничего
    assert all("Ты рада?" in t for t in by_chat.values())
    final = [m.text for m in delivered if m.chat_id == ADMIN][-1]
    assert "Рассылка завершена" in final and "Получателей: 2" in final and "Отправлено: 2" in final

    async with db() as s:
        bc = await s.scalar(select(Broadcast))
        assert (bc.status, bc.segment, bc.total, bc.sent, bc.blocked, bc.failed) == ("done", "girls", 2, 2, 0, 0)
        assert bc.created_by == ADMIN and bc.started_at and bc.finished_at
        report = await render_mailings(s, make_period("all"))
    assert f"#{bc.id}" in report and "отправлено 2, заблокировали 0" in report


async def test_blocked_and_failed_are_counted_and_flagged(tg, db):
    await _setup_audience(tg, db)
    tg.fake.forbidden.add(G1)
    await _start_flow(tg)
    await tg.click(ADMIN, "bc:seg:all")
    await tg.click(ADMIN, "bc:go")
    await tg.click(ADMIN, "bc:yes")
    await _wait_broadcasts()
    async with db() as s:
        bc = await s.scalar(select(Broadcast))
        assert (bc.total, bc.sent, bc.blocked) == (3, 2, 1)
        assert (await s.scalar(select(User).where(User.tg_id == G1))).is_blocked is True
    assert "Заблокировали бота: 1" in sent_texts(tg.fake.calls, ADMIN)[-1]


async def test_retry_after_does_not_lose_messages(tg, db, monkeypatch):
    await _setup_audience(tg, db)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(pushes, "_sleep", fake_sleep)
    tg.fake.retry_once[G2] = 5
    await _start_flow(tg)
    await tg.click(ADMIN, "bc:seg:girls")
    await tg.click(ADMIN, "bc:go")
    await tg.click(ADMIN, "bc:yes")
    await _wait_broadcasts()
    assert slept == [6]
    async with db() as s:
        assert (await s.scalar(select(Broadcast))).sent == 2


async def test_broadcast_goes_through_rate_limiter(tg, db, monkeypatch):
    await _setup_audience(tg, db)
    calls = []

    class CountingLimiter:
        async def acquire(self):
            calls.append(1)

    monkeypatch.setattr(pushes, "limiter", CountingLimiter())
    await _start_flow(tg)
    await tg.click(ADMIN, "bc:seg:all")
    await tg.click(ADMIN, "bc:go")
    await tg.click(ADMIN, "bc:yes")
    await _wait_broadcasts()
    assert len(calls) == 3  # по одному ожиданию лимита на каждое сообщение


async def test_stop_request_cancels_remaining(tg, db):
    await _setup_audience(tg, db)
    async with db() as s:
        bc = Broadcast(created_by=ADMIN, text="hi", segment="all", status="running")
        s.add(bc)
        await s.commit()
        bid = bc.id
    bc_svc.stop_requests.add(bid)
    result = await bc_svc.run_broadcast(tg.bot, db, bid)
    assert result.status == "canceled" and result.sent == 0


async def test_cancel_and_expired_draft(tg):
    await tg.send(ADMIN, "/broadcast")
    assert sent_texts(await tg.send(ADMIN, "/cancel")) == [msg.BC_CANCELLED]
    assert sent_texts(await tg.send(ADMIN, "/cancel")) == [msg.BC_NOTHING_TO_CANCEL]
    calls = await tg.click(ADMIN, "bc:seg:all")  # черновика нет
    assert alerts(calls) == [msg.BC_EXPIRED]


async def test_history_command(tg, db):
    assert sent_texts(await tg.send(ADMIN, "/broadcasts")) == [msg.BC_HISTORY_EMPTY]
    async with db() as s:
        s.add(Broadcast(created_by=ADMIN, text="x", segment="all", status="done", sent=5, blocked=1, failed=0))
        await s.commit()
    assert "отправлено 5, заблокировали 1" in sent_texts(await tg.send(ADMIN, "/broadcasts"))[0]


async def test_telegram_formatting_is_preserved(tg):
    """Если админ оформил текст средствами Telegram (entities), они превращаются в HTML, а не теряются."""
    from tests.conftest import sent_texts as _  # noqa: F401 — только для явности импорта
    await tg.send(ADMIN, "/broadcast")
    now = int(datetime.now().timestamp())
    calls = await tg._feed({"message": {
        "message_id": 2, "date": now, "chat": {"id": ADMIN, "type": "private"}, "from": tg._user(ADMIN, "Админ"),
        "text": "Жирный текст", "entities": [{"type": "bold", "offset": 0, "length": 6}],
    }})
    assert sent_texts(calls)[0] == "<b>Жирный</b> текст"
