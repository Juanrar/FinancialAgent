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
from financial_agent.store.contacts import get_contact
from financial_agent.store.db import utc_now
from financial_agent.store.pending import expire_overdue_routine_actions
from financial_agent.store.routines import (
    describe_routine,
    due_routines,
    mark_sent,
    month_key,
    upcoming_routines,
)
from financial_agent.store.state import get_state, set_state
from financial_agent.transfers import prepare_transfer
from financial_agent.validation import ValidationError

logger = logging.getLogger(__name__)

BALANCE_CHECK_KEY = "balance_check_date"
LOOKAHEAD_DAYS = 3
_REASON_EXPIRED = "venció la sesión"
_PREPARE_EXPIRED = "la sesión de Fiwind venció"


def _reason(error: Exception, expired: str = _REASON_EXPIRED) -> str:
    if isinstance(error, SessionExpired):
        return expired
    if isinstance(error, FiwindUnavailable):
        return f"falló el paso {error.step}"
    if isinstance(error, ValidationError):
        # Los motivos escritos para Claude se traducen; el resto no termina en punto.
        return getattr(error, "for_juan", None) or str(error).rstrip(".")
    return "algo falló"


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]


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
            self._check_balance,
            self._send_routines,
            self._announce_expired,
        )
        for step in steps:
            try:
                await step(now, local)
            except Exception as error:
                logger.error("falló un paso del scheduler (%s): %s", step.__name__, type(error).__name__)

    async def _check_balance(self, now: datetime, local: datetime) -> None:
        """Lee el saldo una vez por día y avisa solo si no alcanza para las rutinas
        de los próximos días. La lectura también sirve de chequeo de sesión."""
        today = local.date().isoformat()
        if (
            not self.wallet.available
            or local.hour < self.settings.routine_hour - 1
            or get_state(self.conn, BALANCE_CHECK_KEY) == today
        ):
            return
        set_state(self.conn, BALANCE_CHECK_KEY, today)
        try:
            balance = await self.wallet.read_balance()
        except SessionExpired:
            await send_session_expired(self.bot, self.settings.telegram_owner_id)
            return
        except FiwindUnavailable as error:
            logger.warning("no se pudo leer el saldo de Fiwind: %s", type(error).__name__)
            return
        if balance is None:
            logger.warning("no se pudo leer el saldo de Fiwind: el panel no lo mostró")
            return
        upcoming = upcoming_routines(
            self.conn, now, days=LOOKAHEAD_DAYS, tz=self.settings.timezone
        )
        needed = sum(routine.amount_cents for routine, _ in upcoming)
        if balance >= needed:
            return
        items = [
            f"{format_amount(routine.amount_cents)} a "
            f"{get_contact(self.conn, routine.contact_id).name} el {when.day}"
            for routine, when in upcoming
        ]
        label = "Rutina" if len(items) == 1 else "Rutinas"
        await self._send(
            f"Te faltan {format_amount(needed - balance)} en Fiwind para las rutinas de los "
            f"próximos días. Saldo: {format_amount(balance)}. {label}: {_join(items)}."
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
