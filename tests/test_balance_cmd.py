from types import SimpleNamespace
from unittest.mock import AsyncMock

from fakes import FakeWallet

from financial_agent.bot.balance_cmd import on_saldo
from financial_agent.bot.fiwind_session import MSG_SESSION_EXPIRED
from financial_agent.fiwind.client import FiwindUnavailable, SessionExpired


def _run(wallet):
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=7))
    bot = SimpleNamespace(send_message=AsyncMock(), send_chat_action=AsyncMock())
    context = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={"wallet": wallet}))
    return update, context


async def test_saldo_shows_balance():
    wallet = FakeWallet(balance=150050)
    update, context = _run(wallet)
    await on_saldo(update, context)
    update.effective_message.reply_text.assert_awaited_once_with(
        "Saldo disponible en Fiwind: $1.500,50"
    )
    assert wallet.calls == [("read_balance",)]


async def test_saldo_session_expired_sends_login_button():
    update, context = _run(FakeWallet(balance_error=SessionExpired("abrir Fiwind")))
    await on_saldo(update, context)
    call = context.bot.send_message.await_args
    assert call.args == (7, MSG_SESSION_EXPIRED)
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "f:login"
    update.effective_message.reply_text.assert_not_awaited()


async def test_saldo_fiwind_unavailable():
    error = FiwindUnavailable("abrir Fiwind", "hay un inicio de sesión esperando el código")
    update, context = _run(FakeWallet(balance_error=error))
    await on_saldo(update, context)
    update.effective_message.reply_text.assert_awaited_once_with(
        "No pude leer el saldo: hay un inicio de sesión esperando el código"
    )


async def test_saldo_not_shown():
    update, context = _run(FakeWallet(balance=None))
    await on_saldo(update, context)
    update.effective_message.reply_text.assert_awaited_once_with("Fiwind no mostró el saldo.")


async def test_saldo_typing_failure_does_not_block_reply():
    update, context = _run(FakeWallet(balance=150050))
    context.bot.send_chat_action.side_effect = RuntimeError("sin red")
    await on_saldo(update, context)
    update.effective_message.reply_text.assert_awaited_once()
