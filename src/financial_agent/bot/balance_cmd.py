import contextlib

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from financial_agent.fiwind.client import FiwindUnavailable, SessionExpired
from financial_agent.money import format_amount

MSG_NOT_SHOWN = "Fiwind no mostró el saldo."


async def on_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat_id = update.effective_chat.id
    # Leer el panel tarda unos segundos. El indicador es cosmético.
    with contextlib.suppress(Exception):
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    wallet = context.application.bot_data["wallet"]
    try:
        balance = await wallet.read_balance()
    except SessionExpired:
        # La wallet ya le mandó a Juan el aviso de sesión vencida.
        return
    except FiwindUnavailable as error:
        await message.reply_text(f"No pude leer el saldo: {error.reason}")
        return
    if balance is None:
        await message.reply_text(MSG_NOT_SHOWN)
        return
    await message.reply_text(f"Saldo disponible en Fiwind: {format_amount(balance)}")
