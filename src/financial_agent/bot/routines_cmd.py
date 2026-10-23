import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from financial_agent.bot.callback_data import make_callback, parse_callback
from financial_agent.store.db import NotFoundError
from financial_agent.store.routines import Routine, delete_routine, describe_routine, list_routines

PREFIX = "r"
MSG_EMPTY = "No hay rutinas. Las creás hablando conmigo."
MSG_GONE = "Esa rutina ya no existe."


def format_routines(conn: sqlite3.Connection, routines: list[Routine]) -> str:
    if not routines:
        return MSG_EMPTY
    lines = [f"{pos}. {describe_routine(conn, r)}" for pos, r in enumerate(routines, start=1)]
    return "\n".join(["Rutinas:", *lines])


def routines_keyboard(routines: list[Routine]) -> InlineKeyboardMarkup | None:
    if not routines:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"Borrar {pos}", callback_data=make_callback(PREFIX, "del", r.id))]
            for pos, r in enumerate(routines, start=1)
        ]
    )


def _render(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup | None]:
    conn = context.application.bot_data["conn"]
    routines = list_routines(conn)
    return format_routines(conn, routines), routines_keyboard(routines)


async def on_rutinas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = _render(context)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def on_routines_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, routine_id = parse_callback(query.data)
    bot_data = context.application.bot_data
    if action == "del":
        try:
            routine = delete_routine(bot_data["conn"], routine_id)
        except NotFoundError:
            await query.message.reply_text(MSG_GONE)
            return
        bot_data["session"].add_context_note(
            f"Juan borró la rutina: {describe_routine(bot_data['conn'], routine)}"
        )
        text, keyboard = _render(context)
        await query.edit_message_text(text, reply_markup=keyboard)
