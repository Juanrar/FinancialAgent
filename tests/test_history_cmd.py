from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from history_helpers import finished_transfer as _finished

from financial_agent.bot.application import COMMANDS
from financial_agent.bot.history_cmd import MSG_EMPTY, on_historial, on_history_callback
from financial_agent.store.contacts import add_destination, create_contact, set_verification

CVU = "0000168300000000123459"


def _dest(conn, name="Fede", alias="fede.mp"):
    c = create_contact(conn, name)
    d = add_destination(conn, c.id, "alias", alias)
    set_verification(conn, d.id, "Federico Pérez", CVU, "Brubank")
    return d


def _context(conn, settings):
    bot_data = {"conn": conn, "settings": settings}
    return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data), user_data={})


def _message():
    return SimpleNamespace(reply_text=AsyncMock())


def _buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


async def test_historial_empty(conn, settings):
    message = _message()
    await on_historial(SimpleNamespace(effective_message=message), _context(conn, settings))
    message.reply_text.assert_awaited_once_with(MSG_EMPTY, reply_markup=None)


async def test_historial_lists_newest_first_with_local_date(conn, settings):
    d = _dest(conn)
    # 01:30 UTC del 28 es el 27 a las 22:30 en Buenos Aires.
    _finished(conn, d, datetime(2026, 10, 3, 15, tzinfo=UTC),
              "Transferencia de $5.000 a Fede, alias fede.mp", "no_salio", "saldo insuficiente")
    _finished(conn, d, datetime(2026, 10, 28, 1, 30, tzinfo=UTC),
              "Transferencia de $2.000 a Fede, alias fede.mp")
    message = _message()
    await on_historial(SimpleNamespace(effective_message=message), _context(conn, settings))
    call = message.reply_text.await_args
    assert call.args[0] == (
        "Últimas transferencias:\n"
        "27/10 $2.000 a Fede, alias fede.mp: enviada\n"
        "03/10 $5.000 a Fede, alias fede.mp: no salió (saldo insuficiente)"
    )
    assert call.kwargs["reply_markup"] is None


async def test_historial_pages_of_ten(conn, settings):
    d = _dest(conn)
    start = datetime(2026, 10, 1, 15, tzinfo=UTC)
    for day in range(12):
        _finished(conn, d, start + timedelta(days=day), f"Transferencia de ${day + 1} a Fede")
    context = _context(conn, settings)
    message = _message()
    await on_historial(SimpleNamespace(effective_message=message), context)
    first = message.reply_text.await_args
    lines = first.args[0].split("\n")
    assert lines[0] == "Últimas transferencias (página 1 de 2):"
    assert lines[1] == "12/10 $12 a Fede: enviada" and len(lines) == 11
    assert _buttons(first.kwargs["reply_markup"]) == [("Anteriores", "h:page:1")]

    query = SimpleNamespace(data="h:page:1", answer=AsyncMock(), edit_message_text=AsyncMock())
    await on_history_callback(SimpleNamespace(callback_query=query), context)
    second = query.edit_message_text.await_args
    assert second.args[0] == (
        "Últimas transferencias (página 2 de 2):\n"
        "02/10 $2 a Fede: enviada\n"
        "01/10 $1 a Fede: enviada"
    )
    assert _buttons(second.kwargs["reply_markup"]) == [("Más nuevas", "h:page:0")]


async def test_historial_page_out_of_range_shows_last_page(conn, settings):
    d = _dest(conn)
    _finished(conn, d, datetime(2026, 10, 1, 15, tzinfo=UTC), "Transferencia de $1 a Fede")
    query = SimpleNamespace(data="h:page:3", answer=AsyncMock(), edit_message_text=AsyncMock())
    await on_history_callback(SimpleNamespace(callback_query=query), _context(conn, settings))
    query.edit_message_text.assert_awaited_once_with(
        "Últimas transferencias:\n01/10 $1 a Fede: enviada", reply_markup=None
    )


def test_commands_include_historial():
    assert "historial" in [c.command for c in COMMANDS]
