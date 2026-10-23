import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from fakes import FakeWallet

from financial_agent.bot.fiwind_session import MSG_SESSION_EXPIRED
from financial_agent.fiwind.client import FiwindUnavailable, SessionExpired
from financial_agent.fiwind.pages import Recipient
from financial_agent.scheduler import Scheduler
from financial_agent.store.contacts import add_destination, create_contact
from financial_agent.store.pending import get_action
from financial_agent.store.routines import create_routine

TZ = "America/Argentina/Buenos_Aires"
CVU = "0000168300000000123459"


def local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ZoneInfo(TZ))


def _routine(conn, day, created, amount=3000000, name="Tío Pepe", alias="tio.pepe"):
    c = create_contact(conn, name)
    add_destination(conn, c.id, "alias", alias)
    return create_routine(conn, c.id, amount, day, hour=10, tz=TZ, now=created)


def _scheduler(settings, conn, wallet):
    bot = SimpleNamespace(send_message=AsyncMock())
    return Scheduler(settings, conn, wallet, bot), bot


def _texts(bot):
    return [c.args[1] for c in bot.send_message.await_args_list]


def _wallet(**kw):
    return FakeWallet(recipient=Recipient("Pedro Gómez", "Brubank", CVU), **kw)


def _routine_messages(bot):
    return [c for c in bot.send_message.await_args_list if c.args[1].startswith("Rutina del mes")]


async def test_routine_sent_once(settings, conn):
    r = _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet())
    await s.tick(local(2026, 11, 5, 9, 59))
    assert not _routine_messages(bot)
    await s.tick(local(2026, 11, 5, 10))
    await s.tick(local(2026, 11, 5, 10, 1))
    sent = _routine_messages(bot)
    assert len(sent) == 1
    assert sent[0].args[0] == 1
    assert sent[0].args[1] == f"Rutina del mes: Transferir $30.000 a Tío Pepe: Pedro Gómez, Brubank, CVU {CVU}"
    action = get_action(conn, int(sent[0].kwargs["reply_markup"].inline_keyboard[0][0].callback_data.split(":")[2]))
    assert action.routine_id == r.id and action.expires_at - action.created_at == timedelta(hours=24)


async def test_routine_confirmation_expiry_is_announced(settings, conn):
    r = _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet())
    await s.tick(local(2026, 11, 5, 10))
    markup = _routine_messages(bot)[0].kwargs["reply_markup"]
    action_id = int(markup.inline_keyboard[0][0].callback_data.split(":")[2])
    await s.tick(local(2026, 11, 6, 10, 1))
    expected = (
        f"Venció la confirmación de la rutina: Transferir $30.000 a Tío Pepe: "
        f"Pedro Gómez, Brubank, CVU {CVU}. Pedímela cuando quieras."
    )
    assert _texts(bot).count(expected) == 1
    action = get_action(conn, action_id)
    assert action.status == "vencida" and action.routine_id == r.id
    await s.tick(local(2026, 11, 6, 10, 2))
    assert _texts(bot).count(expected) == 1


async def test_missed_routine_after_restart(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet())
    await s.tick(local(2026, 11, 8, 15))
    assert len(_routine_messages(bot)) == 1


async def test_routine_prepare_fails(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, FakeWallet(lookup_error=SessionExpired("abrir Fiwind")))
    await s.tick(local(2026, 11, 5, 10))
    texts = _texts(bot)
    assert (
        "No pude preparar la rutina ($30.000 a Tío Pepe los días 5): "
        "la sesión de Fiwind venció. Pedímela cuando quieras."
    ) in texts
    assert MSG_SESSION_EXPIRED in texts
    count = len(texts)
    await s.tick(local(2026, 11, 5, 10, 1))
    assert len(_texts(bot)) == count


async def test_monthly_summary(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12), amount=3000000)
    _routine(conn, day=10, created=local(2026, 10, 1, 12), amount=1500000, name="Ana", alias="ana.mp")
    s, bot = _scheduler(settings, conn, _wallet(balance=300))
    summary = "Este mes tus rutinas suman $45.000. Saldo en Fiwind: $3."
    await s.tick(local(2026, 11, 1, 10))
    assert _texts(bot).count(summary) == 1
    await s.tick(local(2026, 11, 1, 11))
    assert _texts(bot).count(summary) == 1
    await s.tick(local(2026, 12, 1, 10))
    assert _texts(bot).count(summary) == 2


async def test_summary_without_balance(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet(balance_error=SessionExpired("abrir Fiwind")))
    await s.tick(local(2026, 11, 1, 10))
    assert (
        "Este mes tus rutinas suman $30.000. Saldo en Fiwind: no lo pude leer (venció la sesión)."
    ) in _texts(bot)

    s, bot = _scheduler(settings, conn, _wallet(available=False))
    await s.tick(local(2026, 12, 1, 10))
    assert (
        "Este mes tus rutinas suman $30.000. Saldo en Fiwind: "
        "no lo pude leer (agent-browser no está instalado)."
    ) in _texts(bot)


async def test_summary_balance_step_failure_and_missing(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet(balance_error=FiwindUnavailable("leer saldo", "x")))
    await s.tick(local(2026, 11, 1, 10))
    assert (
        "Este mes tus rutinas suman $30.000. Saldo en Fiwind: no lo pude leer (falló el paso leer saldo)."
    ) in _texts(bot)

    s, bot = _scheduler(settings, conn, _wallet(balance=None))
    await s.tick(local(2026, 12, 1, 10))
    assert (
        "Este mes tus rutinas suman $30.000. Saldo en Fiwind: no lo pude leer (Fiwind no mostró el saldo)."
    ) in _texts(bot)


async def test_no_summary_without_routines(settings, conn):
    s, bot = _scheduler(settings, conn, _wallet())
    await s.tick(local(2026, 11, 1, 10))
    assert not any(t.startswith("Este mes") for t in _texts(bot))


async def test_daily_session_check(settings, conn):
    s, bot = _scheduler(settings, conn, _wallet(session_ok=False))
    await s.tick(local(2026, 11, 5, 8, 59))
    assert _texts(bot) == []
    await s.tick(local(2026, 11, 5, 9))
    assert _texts(bot) == [MSG_SESSION_EXPIRED]
    await s.tick(local(2026, 11, 5, 9, 30))
    assert _texts(bot) == [MSG_SESSION_EXPIRED]

    s, bot = _scheduler(settings, conn, _wallet(session_ok=True))
    await s.tick(local(2026, 11, 6, 9))
    assert _texts(bot) == []

    wallet = _wallet(available=False)
    s, bot = _scheduler(settings, conn, wallet)
    await s.tick(local(2026, 11, 7, 9))
    assert ("check_session",) not in wallet.calls and _texts(bot) == []


async def test_session_check_unavailable_sends_nothing_and_tick_continues(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    wallet = _wallet(session_error=FiwindUnavailable("abrir Fiwind", "ventana de login abierta"))
    s, bot = _scheduler(settings, conn, wallet)
    await s.tick(local(2026, 11, 5, 10))
    assert ("check_session",) in wallet.calls
    assert MSG_SESSION_EXPIRED not in _texts(bot)
    assert len(_routine_messages(bot)) == 1


async def test_unexpected_step_error_does_not_skip_later_steps(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet(session_error=RuntimeError("x")))
    await s.tick(local(2026, 11, 5, 10))
    assert len(_routine_messages(bot)) == 1


async def test_loop_survives_errors(settings, conn):
    s, _ = _scheduler(settings, conn, FakeWallet())
    s.interval = 0.01
    calls = 0

    async def boom(now=None):
        nonlocal calls
        calls += 1
        raise RuntimeError("x")

    s.tick = boom
    s.start()
    await asyncio.sleep(0.05)
    await s.stop()
    assert calls >= 2
