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
PAGE_CHARS = 3500
PAGE_NOTES = 10
HEADER_ROOM = 40


def _cut(text: str) -> str:
    limit = PAGE_CHARS - 200
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _line(position: int, note: Note, zone: ZoneInfo | None = None) -> str:
    date = note.updated_at.astimezone(zone) if zone else note.updated_at
    return f"{position}. {_cut(note.text)} ({date:%d/%m/%Y})"


def paginate_notes(notes: list[Note]) -> list[list[tuple[int, Note]]]:
    pages: list[list[tuple[int, Note]]] = []
    page: list[tuple[int, Note]] = []
    size = HEADER_ROOM
    for position, note in enumerate(notes, start=1):
        line_size = len(_line(position, note)) + 1
        if page and (len(page) >= PAGE_NOTES or size + line_size > PAGE_CHARS):
            pages.append(page)
            page, size = [], HEADER_ROOM
        page.append((position, note))
        size += line_size
    if page:
        pages.append(page)
    return pages


def format_notes(page: list[tuple[int, Note]], tz: str, page_index: int, pages: int) -> str:
    if not page:
        return MSG_EMPTY
    zone = ZoneInfo(tz)
    header = "Memoria:" if pages <= 1 else f"Memoria (página {page_index + 1} de {pages}):"
    return "\n".join([header, *(_line(pos, note, zone) for pos, note in page)])


def notes_keyboard(
    page: list[tuple[int, Note]], page_index: int, pages: int
) -> InlineKeyboardMarkup | None:
    if not page:
        return None
    rows = [
        [
            InlineKeyboardButton(f"Editar {pos}", callback_data=make_callback(PREFIX, "edit", note.id)),
            InlineKeyboardButton(f"Borrar {pos}", callback_data=make_callback(PREFIX, "del", note.id)),
        ]
        for pos, note in page
    ]
    nav = []
    if page_index > 0:
        nav.append(
            InlineKeyboardButton("Anterior", callback_data=make_callback(PREFIX, "page", page_index - 1))
        )
    if page_index < pages - 1:
        nav.append(
            InlineKeyboardButton("Siguiente", callback_data=make_callback(PREFIX, "page", page_index + 1))
        )
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _timezone(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.application.bot_data["settings"].timezone


def _render(
    context: ContextTypes.DEFAULT_TYPE, page_index: int = 0, note_id: int | None = None
) -> tuple[str, InlineKeyboardMarkup | None]:
    pages = paginate_notes(list_notes(context.application.bot_data["conn"])) or [[]]
    if note_id is not None:
        page_index = next(
            (i for i, page in enumerate(pages) if any(note.id == note_id for _, note in page)),
            page_index,
        )
    page_index = max(0, min(page_index, len(pages) - 1))
    page = pages[page_index]
    return (
        format_notes(page, _timezone(context), page_index, len(pages)),
        notes_keyboard(page, page_index, len(pages)),
    )


def _page_of(conn, note_id: int) -> int | None:
    pages = paginate_notes(list_notes(conn))
    return next(
        (i for i, page in enumerate(pages) if any(note.id == note_id for _, note in page)), None
    )


async def on_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = _render(context)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def on_memory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, note_id = parse_callback(query.data)
    conn = context.application.bot_data["conn"]

    if action == "page":
        text, keyboard = _render(context, note_id)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "cancel":
        clear_awaiting(context)
        await query.edit_message_text(MSG_CANCELLED)
    elif action == "del":
        page_index = _page_of(conn, note_id)
        try:
            delete_note(conn, note_id)
        except NotFoundError:
            await query.message.reply_text(MSG_GONE)
            return
        text, keyboard = _render(context, page_index or 0)
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
    text, keyboard = _render(context, note_id=awaiting.target_id)
    await message.reply_text(text, reply_markup=keyboard)
