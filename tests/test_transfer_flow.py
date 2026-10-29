import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import FakeWallet
from telegram.error import NetworkError

from financial_agent.bot.chat import on_pending_callback
from financial_agent.bot.fiwind_session import MSG_SESSION_EXPIRED, on_fiwind_callback
from financial_agent.bot.transfer_flow import manual_keyboard, on_manual_callback
from financial_agent.fiwind.client import Expired, Mismatch, NotSent
from financial_agent.fiwind.pages import Recipient
from financial_agent.store import contacts
from financial_agent.store.pending import get_action
from financial_agent.transfers import manual_instructions, prepare_transfer

CVU = "0000168300000000123459"
HOLDER = Recipient("Federico Pérez", "Brubank", CVU)


def _query(data, chat_id=1):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock(), chat_id=chat_id, text="instrucciones"),
    )


def _ctx(conn, wallet, session=None):
    session = session or SimpleNamespace(add_context_note=MagicMock())
    app = SimpleNamespace(bot_data={"conn": conn, "wallet": wallet, "session": session})
    return SimpleNamespace(
        application=app, bot=SimpleNamespace(send_message=AsyncMock()), user_data={}
    )


async def _pending_transfer(conn, wallet):
    contact = contacts.create_contact(conn, "Fede")
    contacts.add_destination(conn, contact.id, "alias", "fede.mp")
    return await prepare_transfer(conn, wallet, contact.id, 200000)


def _update(query):
    return SimpleNamespace(callback_query=query)


def _texts(query):
    return [c.args[0] for c in query.edit_message_text.await_args_list]


async def test_confirm_runs_transfer(conn):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}")
    ctx = _ctx(conn, wallet)
    await on_pending_callback(_update(query), ctx)
    texts = _texts(query)
    assert texts[0] == f"{a.description}\n\nEnviando..."
    assert texts[-1].endswith("Estado: En proceso.")
    assert get_action(conn, a.id).status == "enviada"
    note = ctx.application.bot_data["session"].add_context_note.call_args.args[0]
    assert "Federico" not in note
    assert "Transferencia de $2.000 a Fede, alias fede.mp" in note


async def test_double_tap_withdraws_once(conn):
    wallet = FakeWallet(recipient=HOLDER, delay=0.05)
    a = await _pending_transfer(conn, wallet)
    ctx = _ctx(conn, wallet)
    await asyncio.gather(
        *[on_pending_callback(_update(_query(f"p:ok:{a.id}")), ctx) for _ in range(2)]
    )
    assert [c[0] for c in wallet.calls].count("withdraw") == 1


async def test_second_tap_does_not_overwrite_the_result(conn):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)
    first = _query(f"p:ok:{a.id}")
    await on_pending_callback(_update(first), _ctx(conn, wallet))
    final = _texts(first)[-1]
    assert final.endswith("Estado: En proceso.")
    second = _query(f"p:ok:{a.id}")
    await on_pending_callback(_update(second), _ctx(conn, wallet))
    second.edit_message_text.assert_not_awaited()
    second.answer.assert_awaited_once_with(
        "Esa transferencia ya se está procesando o ya se resolvió."
    )
    assert _texts(first)[-1] == final


async def test_expired_transfer_claim_still_edits(conn):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)
    conn.execute("UPDATE pending_actions SET expires_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    query = _query(f"p:ok:{a.id}")
    await on_pending_callback(_update(query), _ctx(conn, wallet))
    assert _texts(query) == [f"{a.description}\n\nEsta acción venció."]


async def test_claim_edit_failure_is_guarded(conn):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)
    conn.execute("UPDATE pending_actions SET expires_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    query = _query(f"p:ok:{a.id}")
    query.edit_message_text.side_effect = NetworkError("x")
    await on_pending_callback(_update(query), _ctx(conn, wallet))


async def test_answer_failure_does_not_break_confirm(conn):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}")
    query.answer.side_effect = NetworkError("tarde")
    await on_pending_callback(_update(query), _ctx(conn, wallet))
    assert get_action(conn, a.id).status == "enviada"


async def test_answer_failure_in_manual_and_session_callbacks(conn):
    wallet = FakeWallet(recipient=HOLDER, outcome=NotSent("enviar", "no cargó"))
    a = await _pending_transfer(conn, wallet)
    await on_pending_callback(_update(_query(f"p:ok:{a.id}")), _ctx(conn, wallet))
    query = _query(f"t:skip:{a.id}")
    query.answer.side_effect = NetworkError("tarde")
    await on_manual_callback(_update(query), _ctx(conn, wallet))
    assert _texts(query)[-1].endswith("No se hizo.")
    other = _query("f:login")
    other.answer.side_effect = NetworkError("tarde")
    ctx = _ctx(conn, wallet)
    ctx.application.bot_data["wallet"] = FakeWallet()
    await on_fiwind_callback(_update(other), ctx)
    assert _texts(other)[-1] == "Listo, la sesión de Fiwind está activa."


async def test_unexpected_error_marks_unconfirmed(conn, monkeypatch, caplog):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)

    async def boom(*args):
        raise RuntimeError(f"con datos {CVU}")

    monkeypatch.setattr("financial_agent.bot.transfer_flow.run_transfer", boom)
    query = _query(f"p:ok:{a.id}")
    ctx = _ctx(conn, wallet)
    await on_pending_callback(_update(query), ctx)
    after = get_action(conn, a.id)
    assert (after.status, after.detail) == ("sin_confirmar", "error interno")
    assert _texts(query)[-1] == (
        f"{a.description}\n\nNo sé si salió: hubo un error interno después de tocar Confirmar. "
        "Revisá los movimientos de Fiwind antes de pedirla de nuevo."
    )
    ctx.application.bot_data["session"].add_context_note.assert_called_once_with(
        "Resultado de la transferencia (Transferencia de $2.000 a Fede, alias fede.mp): "
        "sin confirmar, error interno"
    )
    assert "RuntimeError" in caplog.text and CVU not in caplog.text


async def test_error_after_send_marks_unconfirmed(conn, monkeypatch):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disco lleno")

    monkeypatch.setattr("financial_agent.transfers.pending.finish_transfer", broken)
    query = _query(f"p:ok:{a.id}")
    await on_pending_callback(_update(query), _ctx(conn, wallet))
    assert [c[0] for c in wallet.calls].count("withdraw") == 1
    assert get_action(conn, a.id).status == "sin_confirmar"
    assert "No sé si salió: hubo un error interno" in _texts(query)[-1]


async def test_cancelled_transfer_propagates(conn, monkeypatch):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)

    async def cancelled(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr("financial_agent.bot.transfer_flow.run_transfer", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await on_pending_callback(_update(_query(f"p:ok:{a.id}")), _ctx(conn, wallet))


async def test_not_sent_sends_manual_buttons(conn):
    wallet = FakeWallet(recipient=HOLDER, outcome=NotSent("enviar", "no cargó"))
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}")
    await on_pending_callback(_update(query), _ctx(conn, wallet))
    assert get_action(conn, a.id).status == "no_salio"
    call = query.message.reply_text.await_args
    assert call.args[0] == manual_instructions(conn, get_action(conn, a.id))
    buttons = call.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == [f"t:done:{a.id}", f"t:skip:{a.id}"]


async def test_expired_sends_session_notice(conn):
    wallet = FakeWallet(recipient=HOLDER, outcome=Expired())
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}", chat_id=7)
    ctx = _ctx(conn, wallet)
    await on_pending_callback(_update(query), ctx)
    call = ctx.bot.send_message.await_args
    assert call.args == (7, MSG_SESSION_EXPIRED)
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "f:login"
    assert query.message.reply_text.await_args.args[0].startswith("Podés hacerla a mano")


async def test_mismatch_sends_follow_up(conn):
    other = Recipient("Otra Persona", "Banco Nuevo", "0000999900000000000011")
    wallet = FakeWallet(recipient=HOLDER, outcome=Mismatch(other))
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}")
    await on_pending_callback(_update(query), _ctx(conn, wallet))
    call = query.message.reply_text.await_args
    assert "Otra Persona" in call.args[0]
    button = call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data.startswith("p:ok:")
    assert button.callback_data != f"p:ok:{a.id}"


async def test_manual_done(conn):
    wallet = FakeWallet(recipient=HOLDER, outcome=NotSent("enviar", "no cargó"))
    a = await _pending_transfer(conn, wallet)
    await on_pending_callback(_update(_query(f"p:ok:{a.id}")), _ctx(conn, wallet))
    session = SimpleNamespace(add_context_note=MagicMock())
    ctx = _ctx(conn, wallet, session)
    query = _query(f"t:done:{a.id}")
    await on_manual_callback(_update(query), ctx)
    assert get_action(conn, a.id).status == "hecha_a_mano"
    assert _texts(query)[-1].endswith("Registrada como hecha a mano.")
    session.add_context_note.assert_called_once_with(
        "Juan hizo a mano: Transferencia de $2.000 a Fede, alias fede.mp"
    )
    again = _query(f"t:done:{a.id}")
    await on_manual_callback(_update(again), ctx)
    assert _texts(again)[-1].endswith("Esa transferencia ya se resolvió.")
    assert session.add_context_note.call_count == 1


async def test_manual_skip(conn):
    wallet = FakeWallet(recipient=HOLDER, outcome=NotSent("enviar", "no cargó"))
    a = await _pending_transfer(conn, wallet)
    await on_pending_callback(_update(_query(f"p:ok:{a.id}")), _ctx(conn, wallet))
    query = _query(f"t:skip:{a.id}")
    await on_manual_callback(_update(query), _ctx(conn, wallet))
    assert _texts(query)[-1].endswith("No se hizo.")
    assert get_action(conn, a.id).status == "no_salio"


async def test_cancel_transfer_note_uses_agent_text(conn):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)
    session = SimpleNamespace(add_context_note=MagicMock())
    await on_pending_callback(_update(_query(f"p:no:{a.id}")), _ctx(conn, wallet, session))
    session.add_context_note.assert_called_once_with(
        "Juan canceló: Transferencia de $2.000 a Fede, alias fede.mp"
    )


def test_manual_keyboard():
    buttons = manual_keyboard(5).inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in buttons] == [
        ("Ya la hice", "t:done:5"),
        ("No la hago", "t:skip:5"),
    ]


async def test_first_edit_failure_still_runs_transfer(conn):
    wallet = FakeWallet(recipient=HOLDER)
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}")
    query.edit_message_text.side_effect = [NetworkError("x"), None]
    ctx = _ctx(conn, wallet)
    await on_pending_callback(_update(query), ctx)
    assert [c[0] for c in wallet.calls].count("withdraw") == 1
    assert get_action(conn, a.id).status == "enviada"
    ctx.application.bot_data["session"].add_context_note.assert_called_once()


async def test_second_edit_failure_still_sends_follow_ups(conn):
    wallet = FakeWallet(recipient=HOLDER, outcome=Expired())
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}", chat_id=7)
    query.edit_message_text.side_effect = [None, NetworkError("x")]
    ctx = _ctx(conn, wallet)
    await on_pending_callback(_update(query), ctx)
    ctx.application.bot_data["session"].add_context_note.assert_called_once()
    assert query.message.reply_text.await_args.args[0].startswith("Podés hacerla a mano")
    assert ctx.bot.send_message.await_args.args == (7, MSG_SESSION_EXPIRED)


async def test_failing_send_does_not_skip_others(conn):
    wallet = FakeWallet(recipient=HOLDER, outcome=Expired())
    a = await _pending_transfer(conn, wallet)
    query = _query(f"p:ok:{a.id}", chat_id=7)
    query.message.reply_text.side_effect = NetworkError("x")
    ctx = _ctx(conn, wallet)
    await on_pending_callback(_update(query), ctx)
    assert ctx.bot.send_message.await_args.args == (7, MSG_SESSION_EXPIRED)
