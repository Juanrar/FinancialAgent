import logging
import sqlite3

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from financial_agent.agent.session import AgentSession
from financial_agent.bot.awaiting import clear_awaiting
from financial_agent.bot.chat import on_pending_callback, on_text
from financial_agent.config import Settings

logger = logging.getLogger(__name__)

MSG_START = (
    "Hola, Juan. Escribime como a una persona. "
    "Con /memory y /contactos ves y editás lo que guardé."
)
MSG_ONLY_TEXT = "Por ahora solo leo texto."
MSG_ERROR = "Algo falló, probá de nuevo."

COMMANDS = [
    BotCommand("memory", "Ver y editar la memoria"),
    BotCommand("contactos", "Ver y editar contactos"),
]


def is_owner(user_id: int | None, owner_id: int) -> bool:
    return user_id is not None and user_id == owner_id


async def owner_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id if user else None
    owner_id = context.application.bot_data["settings"].telegram_owner_id
    if not is_owner(user_id, owner_id):
        logger.warning("mensaje descartado de user_id=%s", user_id)
        raise ApplicationHandlerStop


async def reset_awaiting_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_awaiting(context)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(MSG_START)


async def unsupported_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(MSG_ONLY_TEXT)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Solo la clase: el texto de la excepción puede traer mensajes de Juan o salida del CLI.
    logger.error("error en un handler: %s", type(context.error).__name__)
    if isinstance(update, Update) and update.effective_chat is not None:
        try:
            await context.bot.send_message(update.effective_chat.id, MSG_ERROR)
        except Exception as err:
            logger.error("no pude avisar del error: %s", type(err).__name__)


def register_handlers(application: Application) -> None:
    """Único lugar donde se registran los handlers."""
    application.add_handler(TypeHandler(Update, owner_gate), group=-2)
    application.add_handler(MessageHandler(filters.COMMAND, reset_awaiting_on_command), group=-1)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, unsupported_message))
    application.add_handler(CallbackQueryHandler(on_pending_callback, pattern=r"^p:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(error_handler)


def build_application(
    settings: Settings, conn: sqlite3.Connection, session: AgentSession
) -> Application:
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["conn"] = conn
    application.bot_data["session"] = session
    application.bot_data["awaiting_handlers"] = {}
    register_handlers(application)
    return application


async def start_bot(app: Application) -> None:
    failure = None
    try:
        await app.initialize()
        await app.start()
        await app.bot.set_my_commands(COMMANDS)
        await app.updater.start_polling()
    except Exception as err:
        failure = type(err).__name__
    if failure is not None:
        # Fuera del except para no encadenar la excepción original: su texto puede traer el token.
        raise RuntimeError(f"no se pudo iniciar el bot: {failure}") from None


async def stop_bot(app: Application) -> None:
    if app.updater.running:
        await app.updater.stop()
    if app.running:
        await app.stop()
    await app.shutdown()
