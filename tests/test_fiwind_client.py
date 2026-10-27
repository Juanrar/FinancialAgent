import asyncio
import json

import pytest

from fakes import FakeBrowser
from financial_agent.fiwind.client import (
    AMOUNT_INPUT, DESTINATION_INPUT, DIALOG, LOGIN_URL, PANEL_URL, RESULT_READY_JS,
    RESULT_TEXT_JS, STEP_AMOUNT, STEP_CONFIRM, STEP_OPEN, STEP_RESULT, STEP_SEARCH, STEP_SEND,
    WITHDRAWAL_URL, Expired, FiwindClient, FiwindUnavailable, Insufficient, Mismatch, NotSent,
    SessionExpired, Sent, Unconfirmed, WithdrawalOrder,
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


async def test_login_window():
    client, browser = _client({("get", "url"): PANEL_URL})
    await client.open_login_window()
    assert ("open", LOGIN_URL, True) in browser.calls
    with pytest.raises(FiwindUnavailable):
        await client.lookup("x.y.z")
    assert isinstance(await client.withdraw(ORDER), NotSent)
    assert await client.finish_login() is True
    assert browser.calls[-1] == ("close",)


async def test_read_balance_raises_on_login_window_and_browser_error():
    client, _ = _client({("get", "url"): PANEL_URL})
    await client.open_login_window()
    with pytest.raises(FiwindUnavailable) as info:
        await client.read_balance()
    assert (info.value.step, info.value.reason) == (STEP_OPEN, "la ventana de login está abierta")
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
