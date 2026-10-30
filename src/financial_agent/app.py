import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from telegram.error import TelegramError

from financial_agent.agent.session import AgentSession
from financial_agent.bot.application import build_application, start_bot, stop_bot
from financial_agent.bot.fiwind_session import send_session_expired
from financial_agent.config import Settings, load_settings
from financial_agent.fiwind.browser import AgentBrowser, find_binary
from financial_agent.fiwind.client import Credentials, FiwindClient
from financial_agent.logs import setup_logging
from financial_agent.scheduler import Scheduler
from financial_agent.store.db import connect, migrate
from financial_agent.store.pending import agent_text, expire_all_pending, mark_in_flight_unconfirmed

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, with_bot: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = settings or load_settings()
        setup_logging(
            active.log_level,
            [active.telegram_bot_token, active.claude_oauth_token, active.fiwind_password],
        )
        conn = connect(active.db_path)
        try:
            migrate(conn)
            expire_all_pending(conn)
            interrupted = mark_in_flight_unconfirmed(conn)
            binary = find_binary(active.agent_browser_bin)
            browser = None
            if binary:
                browser = AgentBrowser(
                    binary, "fiwind", active.fiwind_profile_dir, active.fiwind_headed
                )
            else:
                logger.warning(
                    "agent-browser no está instalado: las transferencias van por el camino manual"
                )
            credentials = None
            if active.fiwind_email and active.fiwind_password:
                credentials = Credentials(active.fiwind_email, active.fiwind_password)
            bot = None

            async def notify_session_expired() -> None:
                # La wallet se arma antes que el bot: el aviso usa el bot que haya al avisar.
                if bot is not None:
                    await send_session_expired(bot.bot, active.telegram_owner_id)

            wallet = FiwindClient(browser, credentials, on_session_expired=notify_session_expired)
            session = AgentSession(active, conn, wallet=wallet)
            for action in interrupted:
                session.add_context_note(
                    f"Resultado de la transferencia ({agent_text(action)}): "
                    "sin confirmar, la app se reinició durante el envío"
                )
            scheduler = None
            try:
                if with_bot:
                    bot = build_application(active, conn, session, wallet)
                    await start_bot(bot)
                    logger.info("bot iniciado")
                    for action in interrupted:
                        try:
                            await bot.bot.send_message(
                                active.telegram_owner_id,
                                "La transferencia quedó sin confirmar porque la app se reinició "
                                f"mientras se enviaba: {action.description}. Revisá los "
                                "movimientos de Fiwind antes de pedirla de nuevo.",
                            )
                        except TelegramError as error:
                            logger.error(
                                "no se pudo avisar la transferencia sin confirmar: %s",
                                type(error).__name__,
                            )
                    scheduler = Scheduler(active, conn, wallet, bot.bot)
                    scheduler.start()
                yield
            finally:
                try:
                    if scheduler is not None:
                        await scheduler.stop()
                finally:
                    try:
                        if bot is not None:
                            await stop_bot(bot)
                    finally:
                        await session.close()
        finally:
            conn.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
