from types import SimpleNamespace
from unittest.mock import AsyncMock

from fakes import FakeWallet

from financial_agent.bot.fiwind_session import (
    MSG_SESSION_EXPIRED,
    on_fiwind_callback,
    send_session_expired,
    session_keyboard,
)


def _query(data):
    return SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())


def _ctx(wallet):
    app = SimpleNamespace(bot_data={"wallet": wallet})
    return SimpleNamespace(application=app, user_data={})


def _buttons(call):
    return [(b.text, b.callback_data) for b in call.kwargs["reply_markup"].inline_keyboard[0]]


async def test_open_and_done():
    wallet = FakeWallet(login_ok=True)
    ctx = _ctx(wallet)
    query = _query("f:open")
    await on_fiwind_callback(SimpleNamespace(callback_query=query), ctx)
    assert ("open_login_window",) in wallet.calls
    call = query.edit_message_text.await_args
    assert call.args[0] == (
        "Iniciá sesión en la ventana de Fiwind que se abrió en la PC. "
        "Cuando termines, tocá Listo."
    )
    assert _buttons(call) == [("Listo", "f:done")]

    done = _query("f:done")
    await on_fiwind_callback(SimpleNamespace(callback_query=done), ctx)
    assert ("finish_login",) in wallet.calls
    assert done.edit_message_text.await_args.args[0] == "Listo, la sesión de Fiwind está activa."


async def test_done_without_login():
    wallet = FakeWallet(login_ok=False)
    query = _query("f:done")
    await on_fiwind_callback(SimpleNamespace(callback_query=query), _ctx(wallet))
    call = query.edit_message_text.await_args
    assert call.args[0] == "La sesión de Fiwind sigue sin iniciar."
    assert _buttons(call) == [("Abrir Fiwind", "f:open")]


async def test_open_without_browser():
    wallet = FakeWallet(available=False)
    query = _query("f:open")
    await on_fiwind_callback(SimpleNamespace(callback_query=query), _ctx(wallet))
    assert query.edit_message_text.await_args.args[0] == (
        "No puedo abrir Fiwind: agent-browser no está instalado en la PC."
    )
    assert wallet.calls == []


async def test_send_session_expired():
    bot = SimpleNamespace(send_message=AsyncMock())
    await send_session_expired(bot, 5)
    call = bot.send_message.await_args
    assert call.args == (5, MSG_SESSION_EXPIRED)
    assert call.kwargs["reply_markup"].inline_keyboard == session_keyboard().inline_keyboard
    assert _buttons(call) == [("Abrir Fiwind", "f:open")]
