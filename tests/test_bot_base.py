import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.ext import ApplicationHandlerStop

from financial_agent.bot.application import (
    MSG_ERROR,
    build_application,
    error_handler,
    is_owner,
    owner_gate,
)
from financial_agent.bot.awaiting import (
    Awaiting,
    clear_awaiting,
    get_awaiting,
    set_awaiting,
)
from financial_agent.bot.callback_data import make_callback, parse_callback


def test_is_owner():
    assert is_owner(123, 123)
    assert not is_owner(456, 123)
    assert not is_owner(None, 123)


def test_callback_roundtrip():
    data = make_callback("c", "show", 42)
    assert parse_callback(data) == ("c", "show", 42)
    assert parse_callback(make_callback("m", "cancel")) == ("m", "cancel", None)
    assert len(make_callback("d", "delok", 10**9).encode()) <= 64


def test_awaiting_roundtrip():
    context = SimpleNamespace(user_data={})
    assert get_awaiting(context) is None
    awaiting = Awaiting("note_edit", 5)
    set_awaiting(context, awaiting)
    assert get_awaiting(context) == awaiting
    clear_awaiting(context)
    assert get_awaiting(context) is None
    clear_awaiting(context)


def test_build_application(settings, conn):
    session = object()
    application = build_application(settings, conn, session)
    assert application.bot_data["settings"] is settings
    assert application.bot_data["conn"] is conn
    assert application.bot_data["session"] is session
    assert set(application.bot_data["awaiting_handlers"]) == {"note_edit"}
    assert application.concurrent_updates == 1


def _context(settings):
    application = SimpleNamespace(bot_data={"settings": settings})
    return SimpleNamespace(application=application, user_data={})


async def test_owner_gate_drops_strangers(settings, caplog):
    update = SimpleNamespace(effective_user=SimpleNamespace(id=999))
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ApplicationHandlerStop):
            await owner_gate(update, _context(settings))
    assert "mensaje descartado de user_id=999" in caplog.text


async def test_owner_gate_lets_owner_pass(settings):
    update = SimpleNamespace(effective_user=SimpleNamespace(id=settings.telegram_owner_id))
    await owner_gate(update, _context(settings))


class _ChatUpdate(Update):
    def __init__(self):
        super().__init__(update_id=1)

    @property
    def effective_chat(self):
        return SimpleNamespace(id=7)


async def test_error_handler_logs_class_only_and_replies(caplog):
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(error=ValueError("texto secreto de Juan"), bot=bot)
    with caplog.at_level(logging.ERROR):
        await error_handler(_ChatUpdate(), context)
    bot.send_message.assert_awaited_once_with(7, MSG_ERROR)
    assert "ValueError" in caplog.text
    assert "texto secreto" not in caplog.text


async def test_error_handler_without_chat_does_not_reply(caplog):
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(error=ValueError("x"), bot=bot)
    await error_handler(None, context)
    bot.send_message.assert_not_called()


async def test_start_bot_sanitizes_startup_errors():
    from telegram.error import InvalidToken

    from financial_agent.bot.application import start_bot

    token_error = InvalidToken("The token `1:abc` was rejected by the server.")
    fake = SimpleNamespace(initialize=AsyncMock(side_effect=token_error))
    with pytest.raises(RuntimeError) as info:
        await start_bot(fake)
    assert "1:abc" not in str(info.value)
    assert "InvalidToken" in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None
