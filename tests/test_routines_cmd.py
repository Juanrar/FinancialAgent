from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from financial_agent.bot.application import COMMANDS
from financial_agent.bot.routines_cmd import (
    MSG_EMPTY,
    MSG_GONE,
    format_routines,
    on_routines_callback,
    on_rutinas,
    routines_keyboard,
)
from financial_agent.store import contacts as store
from financial_agent.store.routines import create_routine, list_routines

TZ = "America/Argentina/Buenos_Aires"


def _routine(conn, name="Tío Pepe", amount=3_000_000, day=5):
    contact = store.create_contact(conn, name)
    return create_routine(conn, contact.id, amount, day, hour=9, tz=TZ)


def _context(conn):
    session = SimpleNamespace(add_context_note=MagicMock())
    bot_data = {"conn": conn, "session": session}
    app = SimpleNamespace(bot_data=bot_data)
    return SimpleNamespace(application=app, bot_data=bot_data, user_data={})


def _callback_update(data):
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=message
    )
    return SimpleNamespace(callback_query=query, effective_message=message)


def test_format_routines(conn):
    r = _routine(conn)
    assert format_routines(conn, [r]) == (
        "Rutinas:\n1. $30.000 a Tío Pepe los días 5\nTotal por mes: $30.000"
    )
    assert routines_keyboard([r]).inline_keyboard[0][0].callback_data == f"r:del:{r.id}"
    assert routines_keyboard([]) is None


async def test_on_rutinas_empty(conn):
    message = SimpleNamespace(reply_text=AsyncMock())
    await on_rutinas(SimpleNamespace(effective_message=message), _context(conn))
    message.reply_text.assert_awaited_once_with(MSG_EMPTY, reply_markup=None)


async def test_on_rutinas_lists(conn):
    r = _routine(conn)
    _routine(conn, name="Ana", amount=1_500_000, day=10)
    message = SimpleNamespace(reply_text=AsyncMock())
    await on_rutinas(SimpleNamespace(effective_message=message), _context(conn))
    call = message.reply_text.await_args
    assert call.args[0] == (
        "Rutinas:\n1. $30.000 a Tío Pepe los días 5\n2. $15.000 a Ana los días 10\n"
        "Total por mes: $45.000"
    )
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"r:del:{r.id}"


async def test_delete_callback(conn):
    r = _routine(conn)
    context = _context(conn)
    update = _callback_update(f"r:del:{r.id}")
    await on_routines_callback(update, context)
    assert list_routines(conn) == []
    update.callback_query.edit_message_text.assert_awaited_once_with(MSG_EMPTY, reply_markup=None)
    context.application.bot_data["session"].add_context_note.assert_called_once_with(
        "Juan borró la rutina: $30.000 a Tío Pepe los días 5"
    )
    again = _callback_update(f"r:del:{r.id}")
    await on_routines_callback(again, context)
    again.callback_query.message.reply_text.assert_awaited_once_with(MSG_GONE)
    context.application.bot_data["session"].add_context_note.assert_called_once()


def test_commands_include_rutinas():
    assert "rutinas" in [c.command for c in COMMANDS]
