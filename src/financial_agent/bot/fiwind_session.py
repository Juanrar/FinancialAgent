import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from financial_agent.bot.awaiting import Awaiting, clear_awaiting, set_awaiting
from financial_agent.bot.callback_data import make_callback, parse_callback, safe_answer
from financial_agent.fiwind.client import (
    BadCredentials, CodeRejected, CodeRequired, FiwindUnavailable, LoggedIn,
)

PREFIX = "f"
CODE_AWAITING = Awaiting("fiwind_code", 0)
MSG_SESSION_EXPIRED = "La sesión de Fiwind venció. Tocá Iniciar sesión."
MSG_LOGIN_STARTED = "Iniciando sesión en Fiwind."
MSG_LOGIN_DONE = "Listo, la sesión de Fiwind está activa."
MSG_CODE_REQUESTED = "Fiwind te mandó un código al mail. Mandame los 6 números."
MSG_BAD_CREDENTIALS = (
    "Fiwind dice que el email o la contraseña son incorrectos. "
    "Revisá FIWIND_EMAIL y FIWIND_PASSWORD en el .env."
)
MSG_CODE_FORMAT = "Mandame los 6 números del código que te llegó al mail."
MSG_CODE_CHECKING = "Validando el código."
MSG_CODE_REJECTED = "Fiwind no aceptó el código."
MSG_LOGIN_EXPIRED = "El inicio de sesión venció. Tocá Iniciar sesión para empezar de nuevo."
MSG_NO_BROWSER = "No puedo abrir Fiwind: agent-browser no está instalado en la PC."


def _keyboard(label: str, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=make_callback(PREFIX, action))]]
    )


def session_keyboard() -> InlineKeyboardMarkup:
    return _keyboard("Iniciar sesión", "login")


async def send_session_expired(bot, chat_id: int) -> None:
    await bot.send_message(chat_id, MSG_SESSION_EXPIRED, reply_markup=session_keyboard())


async def on_fiwind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer(query)
    _, action, _ = parse_callback(query.data)
    # "open" es el botón de antes, cuando el login se hacía en una ventana de la PC.
    if action not in ("login", "open"):
        return
    wallet = context.application.bot_data["wallet"]
    if not wallet.available:
        await query.edit_message_text(MSG_NO_BROWSER)
        return
    await query.edit_message_text(MSG_LOGIN_STARTED)
    try:
        outcome = await wallet.start_login()
    except FiwindUnavailable as error:
        await query.edit_message_text(
            f"No pude iniciar sesión en Fiwind: {error.reason}", reply_markup=session_keyboard()
        )
        return
    if isinstance(outcome, CodeRequired):
        set_awaiting(context, CODE_AWAITING)
        await query.edit_message_text(MSG_CODE_REQUESTED)
    elif isinstance(outcome, BadCredentials):
        # Sin botón para reintentar: varios intentos fallidos pueden bloquear la cuenta.
        await query.edit_message_text(MSG_BAD_CREDENTIALS)
    else:
        await query.edit_message_text(MSG_LOGIN_DONE)


async def handle_awaiting(
    update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: Awaiting
) -> None:
    """Recibe el código del mail. No pasa por Claude ni va a los logs."""
    message = update.effective_message
    code = "".join(message.text.split())
    if not re.fullmatch(r"[0-9]{6}", code):
        await message.reply_text(MSG_CODE_FORMAT)
        return
    clear_awaiting(context)
    await message.reply_text(MSG_CODE_CHECKING)
    wallet = context.application.bot_data["wallet"]
    try:
        outcome = await wallet.submit_code(code)
    except FiwindUnavailable as error:
        await message.reply_text(
            f"No pude cargar el código: {error.reason}", reply_markup=session_keyboard()
        )
        return
    if isinstance(outcome, LoggedIn):
        await message.reply_text(MSG_LOGIN_DONE)
    elif isinstance(outcome, CodeRejected):
        await message.reply_text(MSG_CODE_REJECTED, reply_markup=_keyboard("Pedir otro código", "login"))
    else:
        await message.reply_text(MSG_LOGIN_EXPIRED, reply_markup=session_keyboard())
