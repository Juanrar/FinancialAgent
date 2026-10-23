import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from financial_agent.bot.callback_data import make_callback, parse_callback
from financial_agent.bot.fiwind_session import send_session_expired
from financial_agent.store.pending import agent_text, claim_transfer, get_action, mark_done_manually
from financial_agent.transfers import manual_instructions, run_transfer

logger = logging.getLogger(__name__)

PREFIX = "t"
MSG_DONE_MANUALLY = "Registrada como hecha a mano."
MSG_SKIPPED = "No se hizo."
MSG_ALREADY_RESOLVED = "Esa transferencia ya se resolvió."


async def _guarded(step: str, call) -> None:
    """Un error de Telegram no debe cortar el resto del flujo: se loguea solo la clase."""
    try:
        await call
    except TelegramError as error:
        logger.error("falló %s en la confirmación: %s", step, type(error).__name__)


def manual_keyboard(action_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Ya la hice", callback_data=make_callback(PREFIX, "done", action_id)),
                InlineKeyboardButton("No la hago", callback_data=make_callback(PREFIX, "skip", action_id)),
            ]
        ]
    )


async def confirm_transfer(query, context: ContextTypes.DEFAULT_TYPE, action_id: int) -> None:
    # Import tardío: chat importa este módulo para despachar la confirmación.
    from financial_agent.bot.chat import pending_keyboard

    bot_data = context.application.bot_data
    conn = bot_data["conn"]
    resolution = claim_transfer(conn, action_id)
    description = resolution.action.description
    if resolution.outcome != "en_curso":
        await query.edit_message_text(f"{description}\n\n{resolution.message}")
        return

    await _guarded(
        "edit_enviando", query.edit_message_text(f"{description}\n\n{resolution.message}")
    )
    report = await run_transfer(conn, bot_data["wallet"], resolution.action)
    # Primero el aviso al agente: si falla la edición del mensaje, igual se enteró del resultado.
    bot_data["session"].add_context_note(report.agent_note)
    await _guarded(
        "edit_resultado", query.edit_message_text(f"{description}\n\n{report.text}")
    )

    if report.manual_action_id is not None:
        action = get_action(conn, report.manual_action_id)
        await _guarded(
            "instrucciones",
            query.message.reply_text(
                manual_instructions(conn, action), reply_markup=manual_keyboard(action.id)
            ),
        )
    if report.follow_up is not None:
        await _guarded(
            "follow_up",
            query.message.reply_text(
                report.follow_up.description, reply_markup=pending_keyboard(report.follow_up.id)
            ),
        )
    if report.session_expired:
        await _guarded("aviso_sesion", send_session_expired(context.bot, query.message.chat_id))


async def on_manual_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, choice, action_id = parse_callback(query.data)
    bot_data = context.application.bot_data
    conn = bot_data["conn"]
    shown = query.message.text
    if choice == "done":
        if not mark_done_manually(conn, action_id):
            await query.edit_message_text(f"{shown}\n\n{MSG_ALREADY_RESOLVED}")
            return
        bot_data["session"].add_context_note(
            f"Juan hizo a mano: {agent_text(get_action(conn, action_id))}"
        )
        await query.edit_message_text(f"{shown}\n\n{MSG_DONE_MANUALLY}")
    elif get_action(conn, action_id).status == "no_salio":
        await query.edit_message_text(f"{shown}\n\n{MSG_SKIPPED}")
    else:
        await query.edit_message_text(f"{shown}\n\n{MSG_ALREADY_RESOLVED}")
