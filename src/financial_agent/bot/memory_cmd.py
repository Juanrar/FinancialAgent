from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from financial_agent.bot.awaiting import Awaiting, clear_awaiting, set_awaiting
from financial_agent.bot.callback_data import make_callback, parse_callback
from financial_agent.store.db import NotFoundError
from financial_agent.store.memory import Note, delete_note, list_notes, update_note
from financial_agent.validation import ValidationError

PREFIX = "m"
MSG_EMPTY = "No hay notas guardadas."
MSG_GONE = "Esa nota ya no existe."
MSG_UPDATED = "Nota actualizada."
MSG_CANCELLED = "Edición cancelada."


def format_notes(notes: list[Note], tz: str) -> str:
    if not notes:
        return MSG_EMPTY
    zone = ZoneInfo(tz)
    lines = [
        f"{n}. {note.text} ({note.updated_at.astimezone(zone):%d/%m/%Y})"
        for n, note in enumerate(notes, start=1)
    ]
    return "\n".join(["Memoria:", *lines])


def notes_keyboard(notes: list[Note]) -> InlineKeyboardMarkup | None:
    if not notes:
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"Editar {n}", callback_data=make_callback(PREFIX, "edit", note.id)),
                InlineKeyboardButton(f"Borrar {n}", callback_data=make_callback(PREFIX, "del", note.id)),
            ]
            for n, note in enumerate(notes, start=1)
        ]
    )


def _timezone(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.application.bot_data["settings"].timezone


def _render(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup | None]:
    notes = list_notes(context.application.bot_data["conn"])
    return format_notes(notes, _timezone(context)), notes_keyboard(notes)


async def on_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = _render(context)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def on_memory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, note_id = parse_callback(query.data)
    conn = context.application.bot_data["conn"]

    if action == "cancel":
        clear_awaiting(context)
        await query.edit_message_text(MSG_CANCELLED)
    elif action == "del":
        try:
            delete_note(conn, note_id)
        except NotFoundError:
            await query.message.reply_text(MSG_GONE)
            return
        text, keyboard = _render(context)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "edit":
        notes = list_notes(conn)
        position = next((n for n, note in enumerate(notes, start=1) if note.id == note_id), None)
        if position is None:
            await query.message.reply_text(MSG_GONE)
            return
        set_awaiting(context, Awaiting("note_edit", note_id))
        cancel = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Cancelar", callback_data=make_callback(PREFIX, "cancel"))]]
        )
        await query.message.reply_text(
            f"Mandame el texto nuevo para la nota {position}.", reply_markup=cancel
        )


async def handle_awaiting(
    update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: Awaiting
) -> None:
    message = update.effective_message
    conn = context.application.bot_data["conn"]
    try:
        update_note(conn, awaiting.target_id, message.text)
    except ValidationError as error:
        await message.reply_text(str(error))
        return
    except NotFoundError:
        clear_awaiting(context)
        await message.reply_text(MSG_GONE)
        return
    clear_awaiting(context)
    await message.reply_text(MSG_UPDATED)
    text, keyboard = _render(context)
    await message.reply_text(text, reply_markup=keyboard)
