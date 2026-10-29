import asyncio
import json

import pytest

from fakes import FakeBrowser
from financial_agent.fiwind.client import (
    AMOUNT_INPUT, CODE_DONE_JS, CODE_INPUT, DESTINATION_INPUT, DIALOG, EMAIL_INPUT, LOGIN_URL,
    PANEL_URL, PASSWORD_INPUT, RESULT_READY_JS, RESULT_TEXT_JS, STEP_AMOUNT, STEP_CONFIRM,
    STEP_LOGIN, STEP_OPEN, STEP_RESULT, STEP_SEARCH, STEP_SEND, WITHDRAWAL_URL, BadCredentials,
    CodeRejected, CodeRequired, Credentials, Expired, FiwindClient, FiwindUnavailable,
    Insufficient, LoggedIn, LoginExpired, Mismatch, NotSent, SessionExpired, Sent, Unconfirmed,
    WithdrawalOrder,
)
from financial_agent.fiwind.pages import Recipient
from fiwind_samples import (
    BANK, CONFIRM_DIALOG, CVU, DASHBOARD, HOLDER, LOOKUP_FOUND, LOOKUP_NOT_FOUND, RESULT_DIALOG,
)


def _client(responses=None):
    base = {("get", "url"): WITHDRAWAL_URL, ("get", "text", "form"): LOOKUP_FOUND,
            ("get", "text", DIALOG): [CONFIRM_DIALOG], ("eval", RESULT_TEXT_JS): RESULT_EVAL,
            ("snapshot", "-c"): DASHBOARD}
    browser = FakeBrowser(base | (responses or {}))
    return FiwindClient(browser), browser


RESULT_EVAL = json.dumps(RESULT_DIALOG)
ORDER = WithdrawalOrder(CVU, HOLDER, CVU, 1000)
SEND = ("find", "role", "button", "click", "--name", "Enviar", "--exact")
BUSCAR = ("find", "role", "button", "click", "--name", "Buscar")
ENTENDIDO = ("find", "role", "button", "click", "--name", "Entendido")


async def test_lookup_returns_recipient():
    client, browser = _client()
    assert await client.lookup("ana.alias") == Recipient(HOLDER, BANK, CVU)
    assert ("fill", DESTINATION_INPUT, "ana.alias") in browser.calls
    assert browser.calls.index(("wait", DESTINATION_INPUT)) < browser.calls.index(
        ("fill", DESTINATION_INPUT, "ana.alias"))
    assert browser.calls[-1] == ("close",)


async def test_lookup_not_found():
    client, _ = _client({("get", "text", "form"): LOOKUP_NOT_FOUND})
    assert await client.lookup("x.y.z") is None


async def test_lookup_session_expired():
    client, browser = _client({("get", "url"): LOGIN_URL})
    with pytest.raises(SessionExpired):
        await client.lookup("x.y.z")
    assert browser.calls[-1] == ("close",)


async def test_lookup_browser_error():
    client, _ = _client({BUSCAR: RuntimeError()})
    with pytest.raises(FiwindUnavailable) as info:
        await client.lookup("x.y.z")
    assert info.value.step == STEP_SEARCH


async def test_withdraw_sent():
    client, browser = _client()
    assert await client.withdraw(ORDER) == Sent("En proceso")
    assert ("fill", AMOUNT_INPUT, "10") in browser.calls and SEND in browser.calls
    assert browser.calls[-1] == ("close",)


@pytest.mark.parametrize("order", [WithdrawalOrder(CVU, "Otra Persona", CVU, 1000),
                                   WithdrawalOrder(CVU, HOLDER, "0000168300000000678904", 1000)])
async def test_withdraw_mismatch(order):
    client, browser = _client()
    assert await client.withdraw(order) == Mismatch(Recipient(HOLDER, BANK, CVU))
    assert SEND not in browser.calls


async def test_withdraw_not_found():
    client, browser = _client({("get", "text", "form"): LOOKUP_NOT_FOUND})
    outcome = await client.withdraw(ORDER)
    assert isinstance(outcome, NotSent) and outcome.step == STEP_SEARCH
    assert SEND not in browser.calls


async def test_withdraw_insufficient():
    client, browser = _client()
    assert await client.withdraw(WithdrawalOrder(CVU, HOLDER, CVU, 2000)) == Insufficient(1500)
    assert ("find", "role", "button", "click", "--name", "Continuar") not in browser.calls


async def test_withdraw_dialog_amount_mismatch():
    client, browser = _client(
        {("get", "text", DIALOG): [CONFIRM_DIALOG.replace("10,00", "100,00")]})
    outcome = await client.withdraw(ORDER)
    assert isinstance(outcome, NotSent) and outcome.step == STEP_CONFIRM
    assert SEND not in browser.calls


@pytest.mark.parametrize("old,new", [
    (HOLDER, "Otra Persona"),
    (f"CVU\n{CVU}", "CVU\n0000999900000000000011"),
])
async def test_withdraw_dialog_holder_or_account_mismatch(old, new):
    assert old in CONFIRM_DIALOG
    client, browser = _client({("get", "text", DIALOG): [CONFIRM_DIALOG.replace(old, new)]})
    outcome = await client.withdraw(ORDER)
    assert isinstance(outcome, NotSent) and outcome.step == STEP_CONFIRM
    assert SEND not in browser.calls


async def test_withdraw_dialog_unreadable():
    client, browser = _client({("get", "text", DIALOG): ["algo raro"]})
    outcome = await client.withdraw(ORDER)
    assert isinstance(outcome, NotSent) and outcome.step == STEP_CONFIRM
    assert SEND not in browser.calls


async def test_withdraw_fails_before_send():
    client, _ = _client({("fill", AMOUNT_INPUT): RuntimeError()})
    outcome = await client.withdraw(ORDER)
    assert isinstance(outcome, NotSent) and outcome.step == STEP_AMOUNT


async def test_withdraw_fails_on_send_click():
    client, _ = _client({SEND: RuntimeError()})
    assert await client.withdraw(ORDER) == Unconfirmed(STEP_SEND)


async def test_withdraw_reads_result_dialog_not_first_dialog():
    # Si el diálogo de confirmación sigue en el DOM, get text lee ese y no el resultado.
    client, browser = _client({("get", "text", DIALOG): CONFIRM_DIALOG})
    assert await client.withdraw(ORDER) == Sent("En proceso")
    assert browser.calls.index(("wait", "--fn", RESULT_READY_JS)) < browser.calls.index(
        ("eval", RESULT_TEXT_JS))


async def test_withdraw_without_result_screen():
    client, _ = _client({("wait", "--fn", RESULT_READY_JS): RuntimeError()})
    assert await client.withdraw(ORDER) == Unconfirmed(STEP_RESULT)


@pytest.mark.parametrize("output", [json.dumps(""), json.dumps("nada"), "nada"])
async def test_withdraw_unreadable_result(output):
    client, _ = _client({("eval", RESULT_TEXT_JS): output})
    assert await client.withdraw(ORDER) == Unconfirmed(STEP_RESULT)


async def test_withdraw_ignores_error_closing_result():
    client, _ = _client({ENTENDIDO: RuntimeError()})
    assert await client.withdraw(ORDER) == Sent("En proceso")


async def test_withdraw_expired():
    client, _ = _client({("get", "url"): LOGIN_URL})
    assert await client.withdraw(ORDER) == Expired()


async def test_without_browser():
    client = FiwindClient(None)
    assert client.available is False
    assert await client.withdraw(ORDER) == NotSent(STEP_OPEN, "agent-browser no está instalado")
    with pytest.raises(FiwindUnavailable):
        await client.lookup("x.y.z")
    with pytest.raises(FiwindUnavailable) as info:
        await client.read_balance()
    assert info.value.reason == "agent-browser no está instalado"
    with pytest.raises(FiwindUnavailable) as info:
        await client.start_login()
    assert info.value.reason == "agent-browser no está instalado"


async def test_operations_are_serialized():
    client, browser = _client()
    browser.delay = 0.02
    await asyncio.gather(client.lookup("a.b.c"), client.lookup("d.e.f"))
    opens = [i for i, c in enumerate(browser.calls) if c[0] == "open"]
    closes = [i for i, c in enumerate(browser.calls) if c == ("close",)]
    assert opens[1] > closes[2]  # el segundo open llega después del close final del primero


async def test_read_balance():
    client, browser = _client({("get", "url"): PANEL_URL})
    assert await client.read_balance() == 300
    assert ("open", PANEL_URL, None) in browser.calls


async def test_session_expired_in_panel():
    client, _ = _client({("get", "url"): LOGIN_URL})
    with pytest.raises(SessionExpired):
        await client.read_balance()


EMAIL, PASSWORD, CODE = "juan@mail.com", "clave-123", "432802"
AUTHORIZE_URL = "https://panel.fiwind.io/auth/authorize-device"
INGRESAR = ("find", "role", "button", "click", "--name", "Ingresar")
TYPE_EMAIL = ("type", EMAIL_INPUT, EMAIL)
TYPE_PASSWORD = ("type", PASSWORD_INPUT, PASSWORD)
WAITING_CODE = "hay un inicio de sesión esperando el código"


def _login_client(urls, responses=None, clock=None):
    base = {("get", "url"): list(urls), ("get", "text", "form"): LOOKUP_FOUND}
    browser = FakeBrowser(base | (responses or {}))
    client = FiwindClient(browser, Credentials(EMAIL, PASSWORD), clock=clock or (lambda: 0))
    return client, browser


async def test_login_known_device():
    client, browser = _login_client([LOGIN_URL, PANEL_URL])
    assert await client.start_login() == LoggedIn()
    assert ("open", LOGIN_URL, None) in browser.calls
    steps = [browser.calls.index(c) for c in (TYPE_EMAIL, ("wait", "4000"), TYPE_PASSWORD, INGRESAR)]
    assert steps == sorted(steps)
    # Fiwind rechaza el email y la contraseña pegados con fill.
    assert not [c for c in browser.calls if c[0] == "fill"]
    assert browser.calls[-1] == ("close",)


async def test_login_with_session_already_active():
    client, browser = _login_client([PANEL_URL])
    assert await client.start_login() == LoggedIn()
    assert TYPE_EMAIL not in browser.calls
    assert browser.calls[-1] == ("close",)


async def test_login_bad_credentials():
    client, browser = _login_client([LOGIN_URL, LOGIN_URL])
    assert await client.start_login() == BadCredentials()
    assert browser.calls[-1] == ("close",)
    assert await client.lookup("ana.alias") == Recipient(HOLDER, BANK, CVU)


async def test_login_code_required_blocks_other_operations():
    client, browser = _login_client([LOGIN_URL, AUTHORIZE_URL])
    assert await client.start_login() == CodeRequired()
    assert browser.calls[-1] != ("close",)
    with pytest.raises(FiwindUnavailable) as info:
        await client.lookup("ana.alias")
    assert (info.value.step, info.value.reason) == (STEP_OPEN, WAITING_CODE)
    assert await client.withdraw(ORDER) == NotSent(STEP_OPEN, WAITING_CODE)


async def test_submit_code_logs_in():
    client, browser = _login_client([LOGIN_URL, AUTHORIZE_URL, PANEL_URL])
    await client.start_login()
    assert await client.submit_code(CODE) == LoggedIn()
    steps = [browser.calls.index(c) for c in (("click", CODE_INPUT), ("keyboard", "type", CODE))]
    assert steps == sorted(steps)
    assert browser.calls[-1] == ("close",)
    assert await client.lookup("ana.alias") == Recipient(HOLDER, BANK, CVU)


async def test_submit_code_rejected():
    client, browser = _login_client([LOGIN_URL, AUTHORIZE_URL, AUTHORIZE_URL],
                                    {("wait", "--fn", CODE_DONE_JS): RuntimeError()})
    await client.start_login()
    assert await client.submit_code(CODE) == CodeRejected()
    assert browser.calls[-1] == ("close",)
    assert await client.lookup("ana.alias") == Recipient(HOLDER, BANK, CVU)


async def test_submit_code_without_login():
    client, browser = _login_client([])
    assert await client.submit_code(CODE) == LoginExpired()
    assert ("keyboard", "type", CODE) not in browser.calls


async def test_login_waiting_code_expires_after_ten_minutes():
    now = [0]
    client, browser = _login_client([LOGIN_URL, AUTHORIZE_URL], clock=lambda: now[0])
    await client.start_login()
    now[0] = 599
    with pytest.raises(FiwindUnavailable):
        await client.lookup("ana.alias")
    now[0] = 601
    assert await client.lookup("ana.alias") == Recipient(HOLDER, BANK, CVU)
    assert await client.submit_code(CODE) == LoginExpired()
    assert ("keyboard", "type", CODE) not in browser.calls


async def test_login_without_credentials():
    browser = FakeBrowser()
    with pytest.raises(FiwindUnavailable) as info:
        await FiwindClient(browser).start_login()
    assert (info.value.step, info.value.reason) == (
        STEP_LOGIN, "faltan FIWIND_EMAIL o FIWIND_PASSWORD en el .env")
    assert browser.calls == []


async def test_login_browser_error():
    client, browser = _login_client([LOGIN_URL], {("type",): RuntimeError()})
    with pytest.raises(FiwindUnavailable) as info:
        await client.start_login()
    assert (info.value.step, info.value.reason) == (STEP_LOGIN, "falló el navegador")
    assert browser.calls[-1] == ("close",)
    assert await client.lookup("ana.alias") == Recipient(HOLDER, BANK, CVU)


async def test_login_unknown_screen():
    client, browser = _login_client([LOGIN_URL, "https://panel.fiwind.io/auth/otra"])
    with pytest.raises(FiwindUnavailable) as info:
        await client.start_login()
    assert info.value.reason == "la página de Fiwind tiene un formato inesperado"
    assert browser.calls[-1] == ("close",)


async def test_submit_code_browser_error():
    client, browser = _login_client([LOGIN_URL, AUTHORIZE_URL], {("keyboard",): RuntimeError()})
    await client.start_login()
    with pytest.raises(FiwindUnavailable) as info:
        await client.submit_code(CODE)
    assert info.value.step == STEP_LOGIN
    assert browser.calls[-1] == ("close",)
    assert await client.lookup("ana.alias") == Recipient(HOLDER, BANK, CVU)


async def test_read_balance_raises_on_login_waiting_code_and_browser_error():
    client, _ = _login_client([LOGIN_URL, AUTHORIZE_URL])
    await client.start_login()
    with pytest.raises(FiwindUnavailable) as info:
        await client.read_balance()
    assert (info.value.step, info.value.reason) == (STEP_OPEN, WAITING_CODE)
    client, _ = _client({("wait", "--load"): RuntimeError()})
    with pytest.raises(FiwindUnavailable) as info:
        await client.read_balance()
    assert info.value.step == STEP_OPEN


async def test_cancelled_withdraw_keeps_lock_until_thread_finishes():
    client, browser = _client()
    browser.delay = 0.02
    task = asyncio.create_task(client.withdraw(ORDER))
    await asyncio.sleep(0.15)
    other = asyncio.create_task(client.lookup("a.b.c"))
    task.cancel()
    await asyncio.sleep(0)
    assert client._lock.locked()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not other.done() or browser.calls.count(("close",)) >= 2
    await other
    # los comandos del withdraw terminan antes de que empiece el lookup
    first_open = browser.calls.index(("open", WITHDRAWAL_URL, None))
    second_open = browser.calls.index(("open", WITHDRAWAL_URL, None), first_open + 1)
    withdraw_calls = browser.calls[:second_open]
    assert ("fill", DESTINATION_INPUT, "a.b.c") not in withdraw_calls
    # close inicial y final del withdraw, más el close inicial del lookup
    assert withdraw_calls.count(("close",)) == 3
    assert withdraw_calls[-2:] == [("close",), ("close",)]
    assert SEND in withdraw_calls
