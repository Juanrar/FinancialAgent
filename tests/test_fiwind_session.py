from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fakes import FakeWallet

from financial_agent.bot.awaiting import Awaiting, get_awaiting, set_awaiting
from financial_agent.bot.fiwind_session import (
    MSG_SESSION_EXPIRED,
    handle_awaiting,
    on_fiwind_callback,
    send_session_expired,
    session_keyboard,
)
from financial_agent.fiwind.client import (
    BadCredentials, CodeRejected, CodeRequired, FiwindUnavailable, LoginExpired,
)

LOGIN_BUTTON = [("Iniciar sesión", "f:login")]
CODE_AWAITING = Awaiting("fiwind_code", 0)


def _query(data):
    return SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())


def _ctx(wallet):
    app = SimpleNamespace(bot_data={"wallet": wallet})
    return SimpleNamespace(application=app, user_data={})


def _message_update(text):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_message=message)


def _buttons(call):
    markup = call.kwargs.get("reply_markup")
    return [] if markup is None else [(b.text, b.callback_data) for b in markup.inline_keyboard[0]]


async def _tap(wallet, data="f:login"):
    ctx = _ctx(wallet)
    query = _query(data)
    await on_fiwind_callback(SimpleNamespace(callback_query=query), ctx)
    return query.edit_message_text.await_args, ctx


async def _send_code(wallet, text):
    ctx = _ctx(wallet)
    set_awaiting(ctx, CODE_AWAITING)
    update = _message_update(text)
    await handle_awaiting(update, ctx, CODE_AWAITING)
    return update.effective_message.reply_text.await_args, ctx


async def test_login_with_known_device():
    wallet = FakeWallet()
    call, ctx = await _tap(wallet)
    assert wallet.calls == [("start_login",)]
    assert call.args[0] == "Listo, la sesión de Fiwind está activa."
    assert get_awaiting(ctx) is None


async def test_login_asks_for_code():
    call, ctx = await _tap(FakeWallet(login_outcome=CodeRequired()))
    assert call.args[0] == "Fiwind te mandó un código al mail. Mandame los 6 números."
    assert get_awaiting(ctx) == CODE_AWAITING


async def test_login_bad_credentials_does_not_offer_retry():
    call, ctx = await _tap(FakeWallet(login_outcome=BadCredentials()))
    assert call.args[0] == (
        "Fiwind dice que el email o la contraseña son incorrectos. "
        "Revisá FIWIND_EMAIL y FIWIND_PASSWORD en el .env."
    )
    assert _buttons(call) == []
    assert get_awaiting(ctx) is None


async def test_login_unavailable():
    error = FiwindUnavailable("iniciar sesión", "faltan FIWIND_EMAIL o FIWIND_PASSWORD en el .env")
    call, _ = await _tap(FakeWallet(login_error=error))
    assert call.args[0] == (
        "No pude iniciar sesión en Fiwind: faltan FIWIND_EMAIL o FIWIND_PASSWORD en el .env"
    )
    assert _buttons(call) == LOGIN_BUTTON


async def test_login_without_browser():
    wallet = FakeWallet(available=False)
    call, _ = await _tap(wallet)
    assert call.args[0] == "No puedo abrir Fiwind: agent-browser no está instalado en la PC."
    assert wallet.calls == []


async def test_old_open_button_starts_login():
    wallet = FakeWallet()
    await _tap(wallet, "f:open")
    assert wallet.calls == [("start_login",)]


async def test_code_logs_in():
    wallet = FakeWallet()
    call, ctx = await _send_code(wallet, " 432 802 ")
    assert wallet.calls == [("submit_code", "432802")]
    assert call.args[0] == "Listo, la sesión de Fiwind está activa."
    assert get_awaiting(ctx) is None


@pytest.mark.parametrize("text", ["4328", "4328021", "43280a", "٤٣٢٨٠٢"])
async def test_code_must_be_six_digits(text):
    wallet = FakeWallet()
    call, ctx = await _send_code(wallet, text)
    assert call.args[0] == "Mandame los 6 números del código que te llegó al mail."
    assert wallet.calls == []
    assert get_awaiting(ctx) == CODE_AWAITING


async def test_code_rejected_offers_new_code():
    call, ctx = await _send_code(FakeWallet(code_outcome=CodeRejected()), "432802")
    assert call.args[0] == "Fiwind no aceptó el código."
    assert _buttons(call) == [("Pedir otro código", "f:login")]
    assert get_awaiting(ctx) is None


async def test_code_after_login_expired():
    call, ctx = await _send_code(FakeWallet(code_outcome=LoginExpired()), "432802")
    assert call.args[0] == "El inicio de sesión venció. Tocá Iniciar sesión para empezar de nuevo."
    assert _buttons(call) == LOGIN_BUTTON
    assert get_awaiting(ctx) is None


async def test_code_unavailable():
    error = FiwindUnavailable("iniciar sesión", "falló el navegador")
    call, ctx = await _send_code(FakeWallet(code_error=error), "432802")
    assert call.args[0] == "No pude cargar el código: falló el navegador"
    assert _buttons(call) == LOGIN_BUTTON
    assert get_awaiting(ctx) is None


async def test_send_session_expired():
    bot = SimpleNamespace(send_message=AsyncMock())
    await send_session_expired(bot, 5)
    call = bot.send_message.await_args
    assert call.args == (5, MSG_SESSION_EXPIRED)
    assert call.kwargs["reply_markup"].inline_keyboard == session_keyboard().inline_keyboard
    assert _buttons(call) == LOGIN_BUTTON
