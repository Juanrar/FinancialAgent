import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from financial_agent.bot.chat import pending_keyboard
from financial_agent.bot.fiwind_session import send_session_expired
from financial_agent.config import Settings
from financial_agent.fiwind.client import FiwindUnavailable, SessionExpired
from financial_agent.money import format_amount
from financial_agent.store.db import utc_now
from financial_agent.store.pending import expire_overdue_routine_actions
from financial_agent.store.routines import (
    describe_routine,
    due_routines,
    list_routines,
    mark_sent,
    month_key,
    monthly_total,
)
from financial_agent.store.state import get_state, set_state
from financial_agent.transfers import prepare_transfer
from financial_agent.validation import ValidationError

logger = logging.getLogger(__name__)

SESSION_CHECK_KEY = "session_check_date"
SUMMARY_KEY = "summary_month"
_REASON_EXPIRED = "venció la sesión"
_PREPARE_EXPIRED = "la sesión de Fiwind venció"
_REASON_NO_BROWSER = "agent-browser no está instalado"
_REASON_NO_BALANCE = "Fiwind no mostró el saldo"


def _reason(error: Exception, expired: str = _REASON_EXPIRED) -> str:
    if isinstance(error, SessionExpired):
        return expired
    if isinstance(error, FiwindUnavailable):
        return f"falló el paso {error.step}"
    if isinstance(error, ValidationError):
        # Los motivos escritos para Claude se traducen; el resto no termina en punto.
        return getattr(error, "for_juan", None) or str(error).rstrip(".")
    return "algo falló"


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        conn,
        wallet,
        bot,
        clock: Callable[[], datetime] = utc_now,
        interval: float = 60,
    ):
        self.settings = settings
        self.conn = conn
        self.wallet = wallet
        self.bot = bot
        self.clock = clock
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def _send(self, text: str, **kwargs) -> None:
        await self.bot.send_message(self.settings.telegram_owner_id, text, **kwargs)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        local = now.astimezone(ZoneInfo(self.settings.timezone))
        steps = (
            self._check_session,
            self._send_summary,
            self._send_routines,
            self._announce_expired,
        )
        for step in steps:
            try:
                await step(now, local)
            except Exception as error:
                logger.error("falló un paso del scheduler (%s): %s", step.__name__, type(error).__name__)

    async def _check_session(self, now: datetime, local: datetime) -> None:
        today = local.date().isoformat()
        if (
            not self.wallet.available
            or local.hour < self.settings.routine_hour - 1
            or get_state(self.conn, SESSION_CHECK_KEY) == today
        ):
            return
        set_state(self.conn, SESSION_CHECK_KEY, today)
        try:
            ok = await self.wallet.check_session()
        except FiwindUnavailable as error:
            logger.warning("no se pudo chequear la sesión de Fiwind: %s", type(error).__name__)
            return
        if not ok:
            await send_session_expired(self.bot, self.settings.telegram_owner_id)

    async def _balance_text(self) -> str:
        if not self.wallet.available:
            return f"no lo pude leer ({_REASON_NO_BROWSER})"
        try:
            balance = await self.wallet.read_balance()
        except (SessionExpired, FiwindUnavailable) as error:
            return f"no lo pude leer ({_reason(error)})"
        if balance is None:
            return f"no lo pude leer ({_REASON_NO_BALANCE})"
        return format_amount(balance)

    async def _send_summary(self, now: datetime, local: datetime) -> None:
        month = month_key(local)
        if (
            local.hour < self.settings.routine_hour
            or get_state(self.conn, SUMMARY_KEY) == month
            or not list_routines(self.conn)
        ):
            return
        set_state(self.conn, SUMMARY_KEY, month)
        total = format_amount(monthly_total(self.conn))
        await self._send(
            f"Este mes tus rutinas suman {total}. Saldo en Fiwind: {await self._balance_text()}."
        )

    async def _send_routines(self, now: datetime, local: datetime) -> None:
        month = month_key(local)
        due = due_routines(self.conn, now, hour=self.settings.routine_hour, tz=self.settings.timezone)
        for routine in due:
            # Un error al hablar con Telegram en una rutina no frena las demás.
            try:
                await self._run_routine(routine, month, now)
            except Exception as error:
                logger.error(
                    "falló el envío de la rutina %s: %s", routine.id, type(error).__name__
                )

    async def _run_routine(self, routine, month: str, now: datetime) -> None:
        mark_sent(self.conn, routine.id, month)
        try:
            action = await prepare_transfer(
                self.conn,
                self.wallet,
                routine.contact_id,
                routine.amount_cents,
                routine.destination_id,
                routine_id=routine.id,
                now=now,
            )
        except Exception as error:
            logger.warning(
                "no se pudo preparar la rutina %s: %s", routine.id, type(error).__name__
            )
            try:
                await self._send(
                    f"No pude preparar la rutina ({describe_routine(self.conn, routine)}): "
                    f"{_reason(error, _PREPARE_EXPIRED)}. Pedímela cuando quieras."
                )
            finally:
                if isinstance(error, SessionExpired):
                    await send_session_expired(self.bot, self.settings.telegram_owner_id)
            return
        await self._send(
            f"Rutina del mes: {action.description}", reply_markup=pending_keyboard(action.id)
        )

    async def _announce_expired(self, now: datetime, local: datetime) -> None:
        for action in expire_overdue_routine_actions(self.conn, now):
            await self._send(
                f"Venció la confirmación de la rutina: {action.description}. Pedímela cuando quieras."
            )

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as error:
                logger.error("falló el tick del scheduler: %s", type(error).__name__)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
