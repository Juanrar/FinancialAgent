import asyncio
import contextlib

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from financial_agent.agent.session import AgentError
from financial_agent.bot.awaiting import get_awaiting
from financial_agent.bot.callback_data import make_callback, parse_callback
from financial_agent.store.pending import cancel_action, confirm_action

PREFIX = "p"
TYPING_INTERVAL = 4
MSG_NOTHING = "No tengo nada para responder."


def split_message(text: str, limit: int = 4096) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut > 0:
            parts.append(text[:cut])
            text = text[cut + 1 :]
        else:
            parts.append(text[:limit])
            text = text[limit:]
    parts.append(text)
    return parts


def pending_keyboard(action_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirmar", callback_data=make_callback(PREFIX, "ok", action_id)),
                InlineKeyboardButton("Cancelar", callback_data=make_callback(PREFIX, "no", action_id)),
            ]
        ]
    )


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    while True:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(TYPING_INTERVAL)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = get_awaiting(context)
    if awaiting is not None:
        handler = context.application.bot_data["awaiting_handlers"][awaiting.kind]
        await handler(update, context, awaiting)
        return

    message = update.effective_message
    session = context.application.bot_data["session"]
    typing = asyncio.create_task(_keep_typing(context, update.effective_chat.id))
    try:
        reply = await session.send(message.text)
    except AgentError as error:
        await message.reply_text(error.user_message)
        return
    finally:
        typing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing

    if reply.text:
        for part in split_message(reply.text):
            await message.reply_text(part)
    elif not reply.pending:
        await message.reply_text(MSG_NOTHING)
    for action in reply.pending:
        await message.reply_text(action.description, reply_markup=pending_keyboard(action.id))


async def on_pending_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, choice, action_id = parse_callback(query.data)
    conn = context.application.bot_data["conn"]
    resolve = confirm_action if choice == "ok" else cancel_action
    resolution = resolve(conn, action_id)
    description = resolution.action.description
    await query.edit_message_text(f"{description}\n\n{resolution.message}")
    session = context.application.bot_data["session"]
    if resolution.outcome == "confirmada":
        session.add_context_note(f"Juan confirmó: {description}")
    elif resolution.outcome == "cancelada":
        session.add_context_note(f"Juan canceló: {description}")
