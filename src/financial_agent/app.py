import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from financial_agent.agent.session import AgentSession
from financial_agent.bot.application import build_application, start_bot, stop_bot
from financial_agent.config import Settings, load_settings
from financial_agent.logs import setup_logging
from financial_agent.store.db import connect, migrate
from financial_agent.store.pending import expire_all_pending

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, with_bot: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = settings or load_settings()
        setup_logging(active.log_level, [active.telegram_bot_token, active.claude_oauth_token])
        conn = connect(active.db_path)
        try:
            migrate(conn)
            expire_all_pending(conn)
            session = AgentSession(active, conn)
            bot = None
            try:
                if with_bot:
                    bot = build_application(active, conn, session)
                    await start_bot(bot)
                    logger.info("bot iniciado")
                yield
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
