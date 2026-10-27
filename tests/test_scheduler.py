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


def _two_routines(conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12), amount=3000000)
    _routine(conn, day=6, created=local(2026, 10, 1, 12), amount=1500000, name="Ana", alias="ana.mp")


SHORT = (
    "Te faltan $33.000 en Fiwind para las rutinas de los próximos días. Saldo: $12.000. "
    "Rutinas: $30.000 a Tío Pepe el 5 y $15.000 a Ana el 6."
)


async def test_balance_warning_when_short_repeats_daily(settings, conn):
    _two_routines(conn)
    s, bot = _scheduler(settings, conn, _wallet(balance=1200000))
    await s.tick(local(2026, 11, 3, 8, 59))
    assert _texts(bot) == []
    await s.tick(local(2026, 11, 3, 9))
    assert _texts(bot) == [SHORT]
    await s.tick(local(2026, 11, 3, 9, 30))
    assert _texts(bot) == [SHORT]
    await s.tick(local(2026, 11, 4, 9))
    assert _texts(bot) == [SHORT, SHORT]


async def test_balance_warning_only_counts_routines_in_window(settings, conn):
    _two_routines(conn)
    s, bot = _scheduler(settings, conn, _wallet(balance=1200000))
    await s.tick(local(2026, 11, 2, 9))
    assert _texts(bot) == [
        "Te faltan $18.000 en Fiwind para las rutinas de los próximos días. Saldo: $12.000. "
        "Rutina: $30.000 a Tío Pepe el 5."
    ]


async def test_no_message_when_balance_covers(settings, conn):
    _two_routines(conn)
    s, bot = _scheduler(settings, conn, _wallet(balance=4500000))
    await s.tick(local(2026, 11, 1, 9))
    await s.tick(local(2026, 11, 1, 10))
    await s.tick(local(2026, 11, 3, 9))
    assert _texts(bot) == []


async def test_balance_read_daily_without_routines_in_window(settings, conn):
    _routine(conn, day=20, created=local(2026, 10, 1, 12))
    wallet = _wallet(balance=0)
    s, bot = _scheduler(settings, conn, wallet)
    await s.tick(local(2026, 11, 3, 9))
    assert wallet.calls == [("read_balance",)]
    assert _texts(bot) == []


async def test_balance_check_warns_expired_session_once_a_day(settings, conn):
    s, bot = _scheduler(settings, conn, _wallet(balance_error=SessionExpired("abrir Fiwind")))
    await s.tick(local(2026, 11, 5, 9))
    await s.tick(local(2026, 11, 5, 9, 30))
    assert _texts(bot) == [MSG_SESSION_EXPIRED]
    await s.tick(local(2026, 11, 6, 9))
    assert _texts(bot) == [MSG_SESSION_EXPIRED, MSG_SESSION_EXPIRED]


async def test_unreadable_balance_sends_nothing(settings, conn):
    _two_routines(conn)
    s, bot = _scheduler(settings, conn, _wallet(balance=None))
    await s.tick(local(2026, 11, 3, 9))
    assert _texts(bot) == []

    error = FiwindUnavailable("abrir Fiwind", "ventana de login abierta")
    s, bot = _scheduler(settings, conn, _wallet(balance_error=error))
    await s.tick(local(2026, 11, 4, 9))
    assert _texts(bot) == []


async def test_no_balance_check_without_browser(settings, conn):
    _two_routines(conn)
    wallet = _wallet(available=False)
    s, bot = _scheduler(settings, conn, wallet)
    await s.tick(local(2026, 11, 3, 9))
    assert wallet.calls == [] and _texts(bot) == []


async def test_balance_check_failure_does_not_stop_routines(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    error = FiwindUnavailable("abrir Fiwind", "ventana de login abierta")
    s, bot = _scheduler(settings, conn, _wallet(balance_error=error))
    await s.tick(local(2026, 11, 5, 10))
    assert len(_routine_messages(bot)) == 1


async def test_unexpected_step_error_does_not_skip_later_steps(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet(balance_error=RuntimeError("x")))
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


async def test_overdue_routine_action_after_restart_is_announced_once(settings, conn):
    from financial_agent.store.pending import expire_all_pending

    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet())
    await s.tick(local(2026, 11, 5, 10))
    markup = _routine_messages(bot)[0].kwargs["reply_markup"]
    action_id = int(markup.inline_keyboard[0][0].callback_data.split(":")[2])
    # La PC se apaga y vuelve a prenderse después de las 24 horas.
    expire_all_pending(conn, now=local(2026, 11, 7, 9))
    assert get_action(conn, action_id).status == "pendiente"
    s2, bot2 = _scheduler(settings, conn, _wallet())
    await s2.tick(local(2026, 11, 7, 9, 1))
    expected = (
        f"Venció la confirmación de la rutina: Transferir $30.000 a Tío Pepe: "
        f"Pedro Gómez, Brubank, CVU {CVU}. Pedímela cuando quieras."
    )
    assert _texts(bot2).count(expected) == 1
    assert get_action(conn, action_id).status == "vencida"
    await s2.tick(local(2026, 11, 7, 9, 2))
    assert _texts(bot2).count(expected) == 1


async def test_failed_send_for_one_routine_does_not_stop_the_others(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12), name="Tío Pepe", alias="tio.pepe")
    _routine(conn, day=5, created=local(2026, 10, 1, 12), name="Fede", alias="fede.mp")
    s, bot = _scheduler(settings, conn, _wallet())
    calls = {"n": 0}

    async def flaky(chat_id, text, **kwargs):
        if text.startswith("Rutina del mes"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("red caída")

    bot.send_message.side_effect = flaky
    await s.tick(local(2026, 11, 5, 10))
    routine_texts = [t for t in _texts(bot) if t.startswith("Rutina del mes")]
    assert len(routine_texts) == 2  # el primero falló al enviarse, el segundo salió


async def test_failed_prepare_notice_still_sends_session_notice(settings, conn):
    _routine(conn, day=5, created=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, FakeWallet(lookup_error=SessionExpired("abrir Fiwind")))

    async def flaky(chat_id, text, **kwargs):
        if text.startswith("No pude preparar"):
            raise RuntimeError("red caída")

    bot.send_message.side_effect = flaky
    await s.tick(local(2026, 11, 5, 10))
    assert MSG_SESSION_EXPIRED in _texts(bot)


async def test_prepare_failure_text_for_contact_without_destinations(settings, conn):
    c = create_contact(conn, "Tío Pepe")
    create_routine(conn, c.id, 3000000, 5, hour=10, tz=TZ, now=local(2026, 10, 1, 12))
    s, bot = _scheduler(settings, conn, _wallet())
    await s.tick(local(2026, 11, 5, 10))
    assert (
        "No pude preparar la rutina ($30.000 a Tío Pepe los días 5): "
        "Tío Pepe no tiene CBU, CVU ni alias. Pedímela cuando quieras."
    ) in _texts(bot)
