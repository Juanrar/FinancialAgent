import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram.constants import ChatAction

from financial_agent.agent.session import AgentError, AgentReply
from financial_agent.bot import chat
from financial_agent.bot.awaiting import Awaiting, set_awaiting
from financial_agent.bot.chat import (
    on_pending_callback,
    on_text,
    pending_keyboard,
    split_message,
)
from financial_agent.store import contacts
from financial_agent.store.pending import create_action


def test_split_message_respects_limit():
    text = "\n".join(["x" * 100] * 100)
    parts = split_message(text)
    assert all(len(p) <= 4096 for p in parts)
    assert "\n".join(parts) == text


def test_split_message_without_newlines():
    parts = split_message("y" * 9000)
    assert [len(p) for p in parts] == [4096, 4096, 808]


def test_split_short_message():
    assert split_message("hola") == ["hola"]


def test_pending_keyboard():
    kb = pending_keyboard(7)
    buttons = kb.inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in buttons] == [
        ("Confirmar", "p:ok:7"),
        ("Cancelar", "p:no:7"),
    ]


def _text_update(text="hola"):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=1),
    )


def _context(session=None, conn=None, handlers=None):
    bot = SimpleNamespace(send_chat_action=AsyncMock())
    app = SimpleNamespace(
        bot_data={
            "session": session,
            "conn": conn,
            "awaiting_handlers": handlers or {},
        }
    )
    return SimpleNamespace(application=app, bot_data=app.bot_data, bot=bot, user_data={})


async def test_on_text_replies_and_sends_pending(conn):
    contact = contacts.create_contact(conn, "Fede")
    action = create_action(conn, "delete_contact", {"contact_id": contact.id})
    async def send(text):
        await asyncio.sleep(0)
        return AgentReply("listo", [action])

    session = SimpleNamespace(send=send)
    context = _context(session, conn)
    update = _text_update()
    await on_text(update, context)
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == "listo"
    assert calls[1].args[0] == action.description
    assert calls[1].kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"p:ok:{action.id}"
    context.bot.send_chat_action.assert_awaited_with(1, ChatAction.TYPING)


async def test_on_text_empty_reply():
    session = SimpleNamespace(send=AsyncMock(return_value=AgentReply("", [])))
    update = _text_update()
    await on_text(update, _context(session))
    update.effective_message.reply_text.assert_awaited_once_with("No tengo nada para responder.")


async def test_on_text_empty_reply_with_pending_skips_text(conn):
    contact = contacts.create_contact(conn, "Fede")
    action = create_action(conn, "delete_contact", {"contact_id": contact.id})
    session = SimpleNamespace(send=AsyncMock(return_value=AgentReply("", [action])))
    update = _text_update()
    await on_text(update, _context(session, conn))
    update.effective_message.reply_text.assert_awaited_once()
    assert update.effective_message.reply_text.await_args.args[0] == action.description


async def test_on_text_agent_error_sends_user_message(caplog):
    session = SimpleNamespace(send=AsyncMock(side_effect=AgentError("No pude hablar con Claude.")))
    update = _text_update()
    with caplog.at_level(logging.DEBUG):
        await on_text(update, _context(session))
    update.effective_message.reply_text.assert_awaited_once_with("No pude hablar con Claude.")
    assert "Claude" not in caplog.text


async def test_on_text_other_error_propagates_and_stops_typing(monkeypatch):
    monkeypatch.setattr(chat, "TYPING_INTERVAL", 0.01)
    started = asyncio.Event()

    async def boom(text):
        await asyncio.sleep(0.05)
        started.set()
        raise RuntimeError("x")

    session = SimpleNamespace(send=boom)
    context = _context(session)
    try:
        await on_text(_text_update(), context)
    except RuntimeError:
        pass
    else:
        raise AssertionError("debía propagar")
    count = context.bot.send_chat_action.await_count
    assert count >= 2
    await asyncio.sleep(0.05)
    assert context.bot.send_chat_action.await_count == count


async def test_on_text_typing_repeats(monkeypatch):
    monkeypatch.setattr(chat, "TYPING_INTERVAL", 0.01)

    async def slow(text):
        await asyncio.sleep(0.06)
        return AgentReply("ok", [])

    context = _context(SimpleNamespace(send=slow))
    await on_text(_text_update(), context)
    assert context.bot.send_chat_action.await_count >= 2


async def test_on_text_delegates_to_awaiting():
    handler = AsyncMock()
    session = SimpleNamespace(send=AsyncMock())
    context = _context(session, handlers={"note_edit": handler})
    awaiting = Awaiting("note_edit", 3)
    set_awaiting(context, awaiting)
    update = _text_update()
    await on_text(update, context)
    handler.assert_awaited_once_with(update, context, awaiting)
    session.send.assert_not_awaited()


def _callback_update(data):
    query = SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    return SimpleNamespace(callback_query=query), query


async def test_callback_confirm(conn):
    contact = contacts.create_contact(conn, "Fede")
    action = create_action(conn, "delete_contact", {"contact_id": contact.id})
    session = MagicMock()
    update, query = _callback_update(f"p:ok:{action.id}")
    await on_pending_callback(update, _context(session, conn))
    query.answer.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert text == f"{action.description}\n\nListo: {action.description}."
    assert query.edit_message_text.await_args.kwargs.get("reply_markup") is None
    session.add_context_note.assert_called_once_with(f"Juan confirmó: {action.description}")


async def test_callback_cancel(conn):
    contact = contacts.create_contact(conn, "Fede")
    action = create_action(conn, "delete_contact", {"contact_id": contact.id})
    session = MagicMock()
    update, query = _callback_update(f"p:no:{action.id}")
    await on_pending_callback(update, _context(session, conn))
    assert query.edit_message_text.await_args.args[0].endswith("Cancelado.")
    session.add_context_note.assert_called_once_with(f"Juan canceló: {action.description}")


async def test_callback_second_tap_no_note(conn):
    contact = contacts.create_contact(conn, "Fede")
    action = create_action(conn, "delete_contact", {"contact_id": contact.id})
    session = MagicMock()
    context = _context(session, conn)
    await on_pending_callback(_callback_update(f"p:ok:{action.id}")[0], context)
    session.add_context_note.reset_mock()
    update, query = _callback_update(f"p:ok:{action.id}")
    await on_pending_callback(update, context)
    assert query.edit_message_text.await_args.args[0].endswith("Esta acción ya se resolvió.")
    session.add_context_note.assert_not_called()


async def test_on_text_typing_failure_keeps_reply():
    async def slow(text):
        await asyncio.sleep(0.01)
        return AgentReply("ok", [])

    context = _context(SimpleNamespace(send=slow))
    context.bot.send_chat_action = AsyncMock(side_effect=RuntimeError("telegram"))
    update = _text_update()
    await on_text(update, context)
    update.effective_message.reply_text.assert_awaited_once_with("ok")


async def test_on_text_typing_failure_keeps_agent_error_message():
    async def failing(text):
        await asyncio.sleep(0.01)
        raise AgentError("No pude hablar con Claude.")

    context = _context(SimpleNamespace(send=failing))
    context.bot.send_chat_action = AsyncMock(side_effect=RuntimeError("telegram"))
    update = _text_update()
    await on_text(update, context)
    update.effective_message.reply_text.assert_awaited_once_with("No pude hablar con Claude.")
