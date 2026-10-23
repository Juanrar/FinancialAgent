"""Pruebas de enrutado: updates reales de Telegram a través de los handlers registrados."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Message, Update, User

from financial_agent.bot.application import (
    MSG_EDITED,
    MSG_UNKNOWN_COMMAND,
    build_application,
)
from financial_agent.bot.awaiting import Awaiting, get_awaiting, set_awaiting


@pytest.fixture
def app(settings, conn):
    session = SimpleNamespace(send=AsyncMock(), add_context_note=lambda note: None)
    application = build_application(settings, conn, session)
    application.bot_data["awaiting_handlers"] = {"note_edit": AsyncMock()}
    # initialize() llama a Telegram; process_update solo comprueba esta marca.
    application._initialized = True
    application.bot._bot_user = User(id=99, first_name="Bot", is_bot=True, username="fa_bot")
    return application


@pytest.fixture
def replies(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock)
    return mock


def _update(app, user_id, text, *, edited=False, chat_type="private", entities=True):
    message = {
        "message_id": 1,
        "date": 1700000000,
        "chat": {"id": user_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": "J"},
        "text": text,
    }
    if text.startswith("/") and entities:
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    key = "edited_message" if edited else "message"
    message["edit_date"] = 1700000001 if edited else None
    if not edited:
        del message["edit_date"]
    return Update.de_json({"update_id": 1, key: message}, app.bot)


async def test_edited_text_is_not_processed(app, settings, replies):
    owner = settings.telegram_owner_id
    context_like = SimpleNamespace(user_data=app.user_data[owner])
    set_awaiting(context_like, Awaiting("note_edit", 1))
    await app.process_update(_update(app, owner, "nuevo texto", edited=True))
    app.bot_data["session"].send.assert_not_awaited()
    app.bot_data["awaiting_handlers"]["note_edit"].assert_not_awaited()
    assert get_awaiting(context_like) is not None
    replies.assert_awaited_once_with(MSG_EDITED)
    assert MSG_EDITED == "No proceso mensajes editados. Mandalo de nuevo."


async def test_new_text_still_reaches_agent(app, settings, replies):
    app.bot_data["session"].send.return_value = SimpleNamespace(text="listo", pending=[])
    await app.process_update(_update(app, settings.telegram_owner_id, "hola"))
    app.bot_data["session"].send.assert_awaited_once_with("hola")


async def test_unknown_command_gets_reply(app, settings, replies):
    await app.process_update(_update(app, settings.telegram_owner_id, "/nuevo"))
    replies.assert_awaited_once_with(MSG_UNKNOWN_COMMAND)
    assert MSG_UNKNOWN_COMMAND == "No conozco ese comando. Tengo /memory, /contactos y /rutinas."
    app.bot_data["session"].send.assert_not_awaited()


@pytest.mark.parametrize("command", ["/start", "/memory", "/contactos", "/rutinas"])
async def test_known_commands_do_not_get_unknown_reply(app, settings, replies, command):
    await app.process_update(_update(app, settings.telegram_owner_id, command))
    assert all(call.args[0] != MSG_UNKNOWN_COMMAND for call in replies.await_args_list)
    assert replies.await_count >= 1


async def test_owner_in_group_is_dropped(app, settings, replies):
    update = _update(app, settings.telegram_owner_id, "hola", chat_type="group")
    await app.process_update(update)
    replies.assert_not_awaited()
    app.bot_data["session"].send.assert_not_awaited()
