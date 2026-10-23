"""Cliente de Fiwind: secuencia fija de comandos de agent-browser.

Claude no maneja esto. El bot lo llama después de que Juan toca "Confirmar".
Antes de "Enviar" cualquier falla deja todo sin enviar. Desde "Enviar" cualquier
falla es Unconfirmed: no se sabe si la plata salió, y nunca se reintenta.
Los logs llevan solo operación, paso y clase del resultado.
"""

import asyncio
import logging
from dataclasses import dataclass

from financial_agent.fiwind.browser import AgentBrowser
from financial_agent.fiwind.pages import (
    PageFormatError, Recipient, parse_confirm_dialog, parse_dashboard_balance,
    parse_form_balance, parse_lookup, parse_result_dialog, same_holder,
)
from financial_agent.money import amount_input

logger = logging.getLogger(__name__)

LOGIN_URL = "https://panel.fiwind.io/auth/login"
PANEL_URL = "https://panel.fiwind.io/dashboard/home"
WITHDRAWAL_URL = "https://panel.fiwind.io/dashboard/withdrawal/fiat"
DESTINATION_INPUT = "input[placeholder='Ingresá el CBU, CVU o Alias']"
AMOUNT_INPUT = "input[placeholder='Ingresá el monto en pesos']"
DIALOG = "[role=dialog]"
SEARCH_DONE_JS = ("document.body.innerText.includes('Destinatario') || "
                  "document.body.innerText.includes('No se encontraron resultados')")

STEP_OPEN = "abrir Fiwind"
STEP_SEARCH = "buscar el destino"
STEP_BALANCE = "leer el saldo"
STEP_AMOUNT = "cargar el monto"
STEP_CONFIRM = "confirmar el envío"
STEP_SEND = "enviar"
STEP_RESULT = "leer el resultado"

NO_BROWSER = "agent-browser no está instalado"
LOGIN_OPEN = "la ventana de login está abierta"


class SessionExpired(Exception):
    def __init__(self, step: str, reason: str = "la sesión de Fiwind venció"):
        super().__init__(reason)
        self.step = step
        self.reason = reason


class FiwindUnavailable(Exception):
    def __init__(self, step: str, reason: str):
        super().__init__(reason)
        self.step = step
        self.reason = reason


@dataclass(frozen=True)
class WithdrawalOrder:
    destination: str
    holder: str
    account: str
    amount_cents: int


@dataclass(frozen=True)
class Sent:
    status: str


@dataclass(frozen=True)
class NotSent:
    step: str
    reason: str


@dataclass(frozen=True)
class Mismatch:
    found: Recipient


@dataclass(frozen=True)
class Insufficient:
    balance_cents: int


@dataclass(frozen=True)
class Unconfirmed:
    step: str


@dataclass(frozen=True)
class Expired:
    pass


WithdrawalOutcome = Sent | NotSent | Mismatch | Insufficient | Unconfirmed | Expired


def _reason(exc: Exception) -> str:
    if isinstance(exc, PageFormatError):
        return "la página de Fiwind tiene un formato inesperado"
    return "falló el navegador"


class FiwindClient:
    def __init__(self, browser: AgentBrowser | None):
        self._browser = browser
        self._lock = asyncio.Lock()
        self._login_open = False
        self._step = STEP_OPEN

    @property
    def available(self) -> bool:
        return self._browser is not None

    # --- operaciones públicas ---

    async def check_session(self) -> bool:
        if self._browser is None:
            return False
        async with self._lock:
            if self._login_open:
                return False
            try:
                return await asyncio.to_thread(self._check_session_sync)
            except Exception as exc:
                logger.warning("fiwind check_session: paso=%s resultado=%s",
                               self._step, type(exc).__name__)
                return False

    async def lookup(self, destination: str) -> Recipient | None:
        async with self._lock:
            self._require_ready()
            result = await self._guarded("lookup", self._lookup_sync, destination)
            logger.info("fiwind lookup: resultado=%s", "encontrado" if result else "no encontrado")
            return result

    async def withdraw(self, order: WithdrawalOrder) -> WithdrawalOutcome:
        async with self._lock:
            if self._browser is None:
                return NotSent(STEP_OPEN, NO_BROWSER)
            if self._login_open:
                return NotSent(STEP_OPEN, LOGIN_OPEN)
            self._step = STEP_OPEN
            try:
                outcome = await asyncio.to_thread(self._withdraw_sync, order)
            except Exception as exc:
                # No debería pasar: _withdraw_sync atrapa todo. Si pasa, solo
                # sabemos que llegó hasta self._step.
                outcome = (Unconfirmed(self._step) if self._step in (STEP_SEND, STEP_RESULT)
                           else NotSent(self._step, _reason(exc)))
            finally:
                await asyncio.to_thread(self._browser.close)
            logger.info("fiwind withdraw: paso=%s resultado=%s", self._step,
                        type(outcome).__name__)
            return outcome

    async def read_balance(self) -> int | None:
        async with self._lock:
            self._require_ready()
            return await self._guarded("read_balance", self._read_balance_sync)

    async def open_login_window(self) -> None:
        async with self._lock:
            if self._browser is None:
                raise FiwindUnavailable(STEP_OPEN, NO_BROWSER)
            browser = self._browser

            def work() -> None:
                browser.close()
                browser.open(LOGIN_URL, headed=True)

            try:
                await asyncio.to_thread(work)
            except Exception as exc:
                await asyncio.to_thread(browser.close)
                raise FiwindUnavailable(STEP_OPEN, _reason(exc)) from exc
            self._login_open = True
            logger.info("fiwind open_login_window: resultado=abierta")

    async def finish_login(self) -> bool:
        async with self._lock:
            if self._browser is None or not self._login_open:
                return False
            browser = self._browser

            def work() -> bool:
                try:
                    return "/auth/" not in browser.run("get", "url")
                finally:
                    browser.close()

            try:
                ok = await asyncio.to_thread(work)
            except Exception as exc:
                logger.warning("fiwind finish_login: resultado=%s", type(exc).__name__)
                ok = False
            self._login_open = False
            logger.info("fiwind finish_login: resultado=%s", "sesión" if ok else "sin sesión")
            return ok

    # --- soporte ---

    def _require_ready(self) -> None:
        if self._browser is None:
            raise FiwindUnavailable(STEP_OPEN, NO_BROWSER)
        if self._login_open:
            raise FiwindUnavailable(STEP_OPEN, LOGIN_OPEN)

    async def _guarded(self, name: str, fn, *args):
        """Corre fn en un hilo, cierra el navegador y traduce errores."""
        assert self._browser is not None
        self._step = STEP_OPEN
        try:
            return await asyncio.to_thread(fn, *args)
        except SessionExpired:
            logger.info("fiwind %s: paso=%s resultado=SessionExpired", name, self._step)
            raise
        except FiwindUnavailable:
            raise
        except Exception as exc:
            logger.warning("fiwind %s: paso=%s resultado=%s", name, self._step,
                           type(exc).__name__)
            raise FiwindUnavailable(self._step, _reason(exc)) from exc
        finally:
            await asyncio.to_thread(self._browser.close)

    # --- parte sincrónica (corre en un hilo) ---

    def _open_panel(self) -> None:
        browser = self._browser
        browser.close()
        browser.open(PANEL_URL)
        browser.run("wait", "--load", "networkidle")
        if "/auth/" in browser.run("get", "url"):
            raise SessionExpired(STEP_OPEN)

    def _check_session_sync(self) -> bool:
        self._step = STEP_OPEN
        try:
            self._open_panel()
            return True
        except SessionExpired:
            return False
        finally:
            self._browser.close()

    def _read_balance_sync(self) -> int | None:
        self._open_panel()
        self._step = STEP_BALANCE
        return parse_dashboard_balance(self._browser.run("snapshot", "-c"))

    def _open_form(self) -> None:
        browser = self._browser
        browser.close()
        browser.open(WITHDRAWAL_URL)
        browser.run("wait", "--load", "networkidle")
        if "/auth/" in browser.run("get", "url"):
            raise SessionExpired(STEP_OPEN)
        # Abriendo la URL directo, Fiwind no elige moneda y no muestra el destino.
        browser.run("find", "role", "combobox", "click", "--name", "Seleccioná una moneda")
        browser.run("find", "role", "option", "click", "--name", "ARS", "--exact")

    def _search(self, destination: str) -> Recipient | None:
        browser = self._browser
        self._step = STEP_SEARCH
        browser.run("wait", DESTINATION_INPUT)
        browser.run("fill", DESTINATION_INPUT, destination)
        browser.run("find", "role", "button", "click", "--name", "Buscar")
        browser.run("wait", "--fn", SEARCH_DONE_JS)
        return parse_lookup(browser.run("get", "text", "form"))

    def _lookup_sync(self, destination: str) -> Recipient | None:
        self._open_form()
        return self._search(destination)

    def _withdraw_sync(self, order: WithdrawalOrder) -> WithdrawalOutcome:
        browser = self._browser
        try:
            self._step = STEP_OPEN
            self._open_form()
            found = self._search(order.destination)
            if found is None:
                return NotSent(STEP_SEARCH, "Fiwind no encontró el destino")
            if not same_holder(found.holder, order.holder) or found.account != order.account:
                return Mismatch(found)

            self._step = STEP_BALANCE
            balance = parse_form_balance(browser.run("get", "text", "form"))
            if balance is None:
                return NotSent(STEP_BALANCE, "no pude leer el saldo de Fiwind")
            if balance < order.amount_cents:
                return Insufficient(balance)

            self._step = STEP_AMOUNT
            browser.run("wait", AMOUNT_INPUT)
            browser.run("fill", AMOUNT_INPUT, amount_input(order.amount_cents))
            browser.run("find", "role", "button", "click", "--name", "Continuar")

            self._step = STEP_CONFIRM
            browser.run("wait", "--text", "Confirmar envío")
            dialog = parse_confirm_dialog(browser.run("get", "text", DIALOG))
            if (dialog.amount_cents != order.amount_cents
                    or not same_holder(dialog.holder, order.holder)
                    or dialog.account != order.account):
                return NotSent(STEP_CONFIRM, "el diálogo de Fiwind no coincide con la orden")
        except SessionExpired:
            return Expired()
        except Exception as exc:
            return NotSent(self._step, _reason(exc))

        # Desde acá puede haber salido la plata: nada vuelve a NotSent.
        try:
            self._step = STEP_SEND
            browser.run("find", "role", "button", "click", "--name", "Enviar", "--exact")
            self._step = STEP_RESULT
            browser.run("wait", "--text", "Entendido")
            status = parse_result_dialog(browser.run("get", "text", DIALOG))
        except Exception:
            return Unconfirmed(self._step)
        if status is None:
            return Unconfirmed(STEP_RESULT)
        try:
            browser.run("find", "role", "button", "click", "--name", "Entendido")
        except Exception:
            pass
        return Sent(status)
