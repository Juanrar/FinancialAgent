from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from financial_agent.bot.callback_data import make_callback, parse_callback, safe_answer
from financial_agent.store.pending import PendingAction, agent_text, list_transfers, status_label

PREFIX = "h"
PAGE_SIZE = 10
MSG_EMPTY = "No hay transferencias todavía."
_TEXT_PREFIX = "Transferencia de "


def _line(action: PendingAction, zone: ZoneInfo) -> str:
    # Usa agent_text y no la descripción: el historial no repite titular, banco ni CVU.
    when = action.resolved_at.astimezone(zone)
    text = agent_text(action).removeprefix(_TEXT_PREFIX)
    return f"{when:%d/%m} {text}: {status_label(action)}"


def format_history(page: list[PendingAction], tz: str, page_index: int, pages: int) -> str:
    if not page:
        return MSG_EMPTY
    header = "Últimas transferencias:"
    if pages > 1:
        header = f"Últimas transferencias (página {page_index + 1} de {pages}):"
    zone = ZoneInfo(tz)
    return "\n".join([header, *(_line(action, zone) for action in page)])


def history_keyboard(page_index: int, pages: int) -> InlineKeyboardMarkup | None:
    nav = []
    if page_index > 0:
        nav.append(
            InlineKeyboardButton("Más nuevas", callback_data=make_callback(PREFIX, "page", page_index - 1))
        )
    if page_index < pages - 1:
        nav.append(
            InlineKeyboardButton("Anteriores", callback_data=make_callback(PREFIX, "page", page_index + 1))
        )
    return InlineKeyboardMarkup([nav]) if nav else None


def _render(
    context: ContextTypes.DEFAULT_TYPE, page_index: int = 0
) -> tuple[str, InlineKeyboardMarkup | None]:
    bot_data = context.application.bot_data
    transfers = list_transfers(bot_data["conn"])
    pages = max(1, -(-len(transfers) // PAGE_SIZE))
    page_index = max(0, min(page_index, pages - 1))
    page = transfers[page_index * PAGE_SIZE : (page_index + 1) * PAGE_SIZE]
    return (
        format_history(page, bot_data["settings"].timezone, page_index, pages),
        history_keyboard(page_index, pages),
    )


async def on_historial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = _render(context)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def on_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer(query)
    _, _, page_index = parse_callback(query.data)
    text, keyboard = _render(context, page_index)
    await query.edit_message_text(text, reply_markup=keyboard)
