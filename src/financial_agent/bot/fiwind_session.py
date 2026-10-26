from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from financial_agent.bot.callback_data import make_callback, parse_callback, safe_answer
from financial_agent.fiwind.client import FiwindUnavailable

PREFIX = "f"
MSG_SESSION_EXPIRED = (
    "La sesión de Fiwind venció. Tocá Abrir Fiwind, iniciá sesión en la ventana "
    "que aparece en la PC y después tocá Listo."
)
MSG_LOGIN_OPENED = (
    "Iniciá sesión en la ventana de Fiwind que se abrió en la PC. Cuando termines, tocá Listo."
)
MSG_LOGIN_DONE = "Listo, la sesión de Fiwind está activa."
MSG_LOGIN_PENDING = "La sesión de Fiwind sigue sin iniciar."
MSG_NO_BROWSER = "No puedo abrir Fiwind: agent-browser no está instalado en la PC."


def _keyboard(label: str, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=make_callback(PREFIX, action))]]
    )


def session_keyboard() -> InlineKeyboardMarkup:
    return _keyboard("Abrir Fiwind", "open")


async def send_session_expired(bot, chat_id: int) -> None:
    await bot.send_message(chat_id, MSG_SESSION_EXPIRED, reply_markup=session_keyboard())


async def on_fiwind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer(query)
    _, action, _ = parse_callback(query.data)
    wallet = context.application.bot_data["wallet"]
    if action == "open":
        if not wallet.available:
            await query.edit_message_text(MSG_NO_BROWSER)
            return
        try:
            await wallet.open_login_window()
        except FiwindUnavailable as error:
            await query.edit_message_text(f"No puedo abrir Fiwind: {error.reason}")
            return
        await query.edit_message_text(MSG_LOGIN_OPENED, reply_markup=_keyboard("Listo", "done"))
    elif action == "done":
        if await wallet.finish_login():
            await query.edit_message_text(MSG_LOGIN_DONE)
        else:
            await query.edit_message_text(MSG_LOGIN_PENDING, reply_markup=session_keyboard())
