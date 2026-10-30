"""Cliente de Fiwind: secuencia fija de comandos de agent-browser.

Claude no maneja esto. El bot lo llama después de que Juan toca "Confirmar".
Antes de "Enviar" cualquier falla deja todo sin enviar. Desde "Enviar" cualquier
falla es Unconfirmed: no se sabe si la plata salió, y nunca se reintenta.
Los logs llevan solo operación, paso y clase del resultado.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from financial_agent.fiwind.browser import AgentBrowser, BrowserCommandError
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
# get text lee solo el primer [role=dialog], y al aparecer el resultado el diálogo de
# confirmación puede seguir en el DOM. Se busca el diálogo con "Estado" y un valor debajo.
_RESULT_DIALOG_JS = ("[...document.querySelectorAll('[role=dialog]')].map(d => d.innerText)"
                     ".find(t => { const l = t.split('\\n').map(s => s.trim()).filter(Boolean);"
                     " const i = l.indexOf('Estado'); return i >= 0 && i < l.length - 1; })")
RESULT_READY_JS = f"{_RESULT_DIALOG_JS} !== undefined"
RESULT_TEXT_JS = f"{_RESULT_DIALOG_JS} ?? ''"
EMAIL_INPUT = "input[placeholder='Tu email']"
PASSWORD_INPUT = "input[type=password]"
# Seis casillas de un dígito. Al completar la sexta, Fiwind valida solo.
CODE_INPUT = "input[inputmode=numeric]"
TYPING_PAUSE_MS = "4000"
LOGIN_DONE_JS = ("location.pathname !== '/auth/login' || "
                 "document.body.innerText.includes('incorrectos')")
# Si Fiwind no acepta el código, la página no sale de /auth/ y el wait vence a los 30 s.
CODE_DONE_JS = "!location.pathname.startsWith('/auth/')"
AUTHORIZE_PATH = "/auth/authorize-device"
LOGIN_TIMEOUT = 600
# Un turno del agente o del scheduler puede encontrar la sesión vencida varias veces seguidas.
NOTICE_INTERVAL = 600

STEP_LOGIN = "iniciar sesión"
STEP_OPEN = "abrir Fiwind"
STEP_SEARCH = "buscar el destino"
STEP_BALANCE = "leer el saldo"
STEP_AMOUNT = "cargar el monto"
STEP_CONFIRM = "confirmar el envío"
STEP_SEND = "enviar"
STEP_RESULT = "leer el resultado"

NO_BROWSER = "agent-browser no está instalado"
NO_CREDENTIALS = "faltan FIWIND_EMAIL o FIWIND_PASSWORD en el .env"
LOGIN_OPEN = "hay un inicio de sesión esperando el código"


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


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class LoggedIn:
    pass


@dataclass(frozen=True)
class CodeRequired:
    pass


@dataclass(frozen=True)
class BadCredentials:
    pass


@dataclass(frozen=True)
class CodeRejected:
    pass


@dataclass(frozen=True)
class LoginExpired:
    pass


LoginOutcome = LoggedIn | CodeRequired | BadCredentials
CodeOutcome = LoggedIn | CodeRejected | LoginExpired


def _reason(exc: Exception) -> str:
    if isinstance(exc, PageFormatError):
        return "la página de Fiwind tiene un formato inesperado"
    return "falló el navegador"


class FiwindClient:
    def __init__(self, browser: AgentBrowser | None, credentials: Credentials | None = None,
                 clock=time.monotonic,
                 on_session_expired: Callable[[], Awaitable[None]] | None = None):
        self._browser = browser
        self._credentials = credentials
        self._clock = clock
        # Aviso de sesión vencida: lo manda la wallet, no quien llama.
        self._on_session_expired = on_session_expired
        self._notice_sent_at: float | None = None
        self._lock = asyncio.Lock()
        # Login esperando el código del mail: el navegador queda abierto en esa pantalla.
        self._login_open = False
        self._login_started = 0.0
        self._step = STEP_OPEN

    @property
    def available(self) -> bool:
        return self._browser is not None

    # --- operaciones públicas ---

    async def lookup(self, destination: str) -> Recipient | None:
        async with self._lock:
            await self._require_ready()
            result = await self._guarded("lookup", self._lookup_sync, destination)
            logger.info("fiwind lookup: resultado=%s", "encontrado" if result else "no encontrado")
            return result

    async def withdraw(self, order: WithdrawalOrder) -> WithdrawalOutcome:
        async with self._lock:
            await self._drop_stale_login()
            if self._browser is None:
                return NotSent(STEP_OPEN, NO_BROWSER)
            if self._login_open:
                return NotSent(STEP_OPEN, LOGIN_OPEN)
            self._step = STEP_OPEN
            try:
                outcome = await self._exclusive("withdraw", self._withdraw_sync, order)
            except Exception as exc:
                # No debería pasar: _withdraw_sync atrapa todo. Si pasa, solo
                # sabemos que llegó hasta self._step.
                outcome = (Unconfirmed(self._step) if self._step in (STEP_SEND, STEP_RESULT)
                           else NotSent(self._step, _reason(exc)))
            logger.info("fiwind withdraw: paso=%s resultado=%s", self._step,
                        type(outcome).__name__)
            if isinstance(outcome, Expired):
                await self._notify_expired()
            return outcome

    async def read_balance(self) -> int | None:
        async with self._lock:
            await self._require_ready()
            return await self._guarded("read_balance", self._read_balance_sync)

    async def start_login(self) -> LoginOutcome:
        """Carga email y contraseña. Si Fiwind pide el código, deja el navegador en esa pantalla."""
        async with self._lock:
            if self._browser is None:
                raise FiwindUnavailable(STEP_LOGIN, NO_BROWSER)
            if self._credentials is None:
                raise FiwindUnavailable(STEP_LOGIN, NO_CREDENTIALS)
            self._login_open = False
            try:
                outcome = await asyncio.to_thread(self._start_login_sync)
            except Exception as exc:
                await asyncio.to_thread(self._browser.close)
                logger.warning("fiwind start_login: resultado=%s", type(exc).__name__)
                raise FiwindUnavailable(STEP_LOGIN, _reason(exc)) from exc
            if isinstance(outcome, CodeRequired):
                self._login_open = True
                self._login_started = self._clock()
            else:
                await asyncio.to_thread(self._browser.close)
            if isinstance(outcome, LoggedIn):
                self._notice_sent_at = None
            logger.info("fiwind start_login: resultado=%s", type(outcome).__name__)
            return outcome

    async def submit_code(self, code: str) -> CodeOutcome:
        async with self._lock:
            await self._drop_stale_login()
            if self._browser is None or not self._login_open:
                return LoginExpired()
            try:
                outcome = await asyncio.to_thread(self._submit_code_sync, code)
            except Exception as exc:
                logger.warning("fiwind submit_code: resultado=%s", type(exc).__name__)
                raise FiwindUnavailable(STEP_LOGIN, _reason(exc)) from exc
            finally:
                self._login_open = False
                await asyncio.to_thread(self._browser.close)
            if isinstance(outcome, LoggedIn):
                self._notice_sent_at = None
            logger.info("fiwind submit_code: resultado=%s", type(outcome).__name__)
            return outcome

    # --- soporte ---

    async def _notify_expired(self) -> None:
        if self._on_session_expired is None:
            return
        if (self._notice_sent_at is not None
                and self._clock() - self._notice_sent_at < NOTICE_INTERVAL):
            return
        # Un aviso que falla no cambia el resultado de la operación y se reintenta en la próxima.
        try:
            await self._on_session_expired()
        except Exception as exc:
            logger.warning("fiwind aviso de sesión vencida: resultado=%s", type(exc).__name__)
            return
        self._notice_sent_at = self._clock()

    async def _drop_stale_login(self) -> None:
        if self._login_open and self._clock() - self._login_started > LOGIN_TIMEOUT:
            self._login_open = False
            await asyncio.to_thread(self._browser.close)
            logger.info("fiwind login: resultado=vencido sin código")

    async def _require_ready(self) -> None:
        await self._drop_stale_login()
        if self._browser is None:
            raise FiwindUnavailable(STEP_OPEN, NO_BROWSER)
        if self._login_open:
            raise FiwindUnavailable(STEP_OPEN, LOGIN_OPEN)

    async def _exclusive(self, name: str, fn, *args):
        """Corre fn y el cierre del navegador en un solo hilo.

        Si cancelan la tarea, el hilo no se puede frenar: se espera acá, con el
        lock tomado, a que termine antes de propagar la cancelación.
        """
        browser = self._browser

        def job():
            try:
                return fn(*args)
            finally:
                browser.close()

        fut = asyncio.ensure_future(asyncio.to_thread(job))
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            while not fut.done():
                try:
                    await asyncio.wait([fut])
                except asyncio.CancelledError:
                    pass
            result = None if fut.cancelled() or fut.exception() else fut.result()
            logger.warning("fiwind %s: cancelada, paso=%s resultado=%s", name, self._step,
                           type(result).__name__)
            raise

    async def _guarded(self, name: str, fn, *args):
        """Corre fn con cierre del navegador y traduce errores."""
        self._step = STEP_OPEN
        try:
            return await self._exclusive(name, fn, *args)
        except SessionExpired:
            logger.info("fiwind %s: paso=%s resultado=SessionExpired", name, self._step)
            await self._notify_expired()
            raise
        except FiwindUnavailable:
            raise
        except Exception as exc:
            logger.warning("fiwind %s: paso=%s resultado=%s", name, self._step,
                           type(exc).__name__)
            raise FiwindUnavailable(self._step, _reason(exc)) from exc

    # --- parte sincrónica (corre en un hilo) ---

    def _start_login_sync(self) -> LoginOutcome:
        browser = self._browser
        browser.close()
        browser.open(LOGIN_URL)
        browser.run("wait", "--load", "networkidle")
        if "/auth/" not in browser.run("get", "url"):
            return LoggedIn()
        # Tecla por tecla: con fill, Fiwind responde que el usuario o la contraseña
        # son incorrectos aunque el valor quede bien cargado.
        browser.run("type", EMAIL_INPUT, self._credentials.email)
        browser.run("wait", TYPING_PAUSE_MS)
        browser.run("type", PASSWORD_INPUT, self._credentials.password)
        browser.run("find", "role", "button", "click", "--name", "Ingresar")
        browser.run("wait", "--fn", LOGIN_DONE_JS)
        url = browser.run("get", "url")
        if AUTHORIZE_PATH in url:
            return CodeRequired()
        if "/auth/login" in url:
            return BadCredentials()
        if "/auth/" in url:
            raise PageFormatError("pantalla de login desconocida")
        return LoggedIn()

    def _submit_code_sync(self, code: str) -> CodeOutcome:
        browser = self._browser
        browser.run("click", CODE_INPUT)
        browser.run("keyboard", "type", code)
        try:
            browser.run("wait", "--fn", CODE_DONE_JS)
        except BrowserCommandError:
            pass  # Se decide por la URL.
        return CodeRejected() if "/auth/" in browser.run("get", "url") else LoggedIn()

    def _open_panel(self) -> None:
        browser = self._browser
        browser.close()
        browser.open(PANEL_URL)
        browser.run("wait", "--load", "networkidle")
        if "/auth/" in browser.run("get", "url"):
            raise SessionExpired(STEP_OPEN)

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
            browser.run("wait", "--fn", RESULT_READY_JS)
            status = parse_result_dialog(json.loads(browser.run("eval", RESULT_TEXT_JS)))
        except Exception:
            return Unconfirmed(self._step)
        if status is None:
            return Unconfirmed(STEP_RESULT)
        try:
            browser.run("find", "role", "button", "click", "--name", "Entendido")
        except Exception:
            pass
        return Sent(status)
