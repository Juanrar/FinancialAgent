from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeWallet

from financial_agent.fiwind.client import (
    STEP_AMOUNT, STEP_SEND, Expired, Insufficient, Mismatch, NotSent, Sent, Unconfirmed,
    WithdrawalOrder,
)
from financial_agent.fiwind.pages import Recipient
from financial_agent.store.contacts import (
    add_destination, create_contact, delete_destination, get_contact, get_destination,
    set_verification,
)
from financial_agent.store.pending import agent_text, claim_transfer, get_action
from financial_agent.store.routines import create_routine
from financial_agent.transfers import (
    choose_destination, manual_instructions, prepare_transfer, run_transfer,
)
from financial_agent.validation import ValidationError

T0 = datetime(2026, 10, 23, 12, tzinfo=UTC)
FEDE_HOLDER, FEDE_CVU = "Federico Pérez", "0000168300000000123459"
FEDE = Recipient(FEDE_HOLDER, "Brubank", FEDE_CVU)


def _fede(conn, *dests):
    c = create_contact(conn, "Fede")
    made = [add_destination(conn, c.id, t, v) for t, v in dests]
    return get_contact(conn, c.id), made


def test_choose_primary_or_single(conn):
    c, (a, b) = _fede(conn, ("alias", "fede.mp"), ("alias", "fede.uala"))
    assert choose_destination(c, None).id == a.id
    assert choose_destination(c, b.id).id == b.id


def test_choose_ignores_email_primary(conn):
    c, (email, alias) = _fede(conn, ("email_mp", "fede@mail.com"), ("alias", "fede.mp"))
    assert choose_destination(c, None).id == alias.id


def test_choose_errors(conn):
    c, (email,) = _fede(conn, ("email_mp", "fede@mail.com"))
    with pytest.raises(ValidationError, match="no transfiere a emails"):
        choose_destination(c, None)
    with pytest.raises(ValidationError, match="no transfiere a emails"):
        choose_destination(c, email.id)
    with pytest.raises(ValidationError, match="no es de Fede"):
        choose_destination(c, 999)
    empty, _ = _fede(conn)
    with pytest.raises(ValidationError, match="no tiene destinos"):
        choose_destination(empty, None)


def test_choose_several_without_primary(conn):
    c, (main, a, b) = _fede(
        conn, ("alias", "fede.main"), ("alias", "fede.mp"), ("alias", "fede.uala")
    )
    delete_destination(conn, main.id)
    c = get_contact(conn, c.id)
    with pytest.raises(ValidationError, match="ninguno es principal") as error:
        choose_destination(c, None)
    assert f"id={a.id}" in str(error.value) and f"id={b.id}" in str(error.value)


async def test_prepare_verifies_once(conn):
    c, (d,) = _fede(conn, ("alias", "fede.mp"))
    wallet = FakeWallet(recipient=FEDE)
    a = await prepare_transfer(conn, wallet, c.id, 200000, now=T0)
    assert FEDE_HOLDER in a.description and FEDE_HOLDER not in agent_text(a)
    assert agent_text(a) == "Transferencia de $2.000 a Fede, alias fede.mp"
    await prepare_transfer(conn, wallet, c.id, 200000, now=T0)
    assert [x[0] for x in wallet.calls].count("lookup") == 1


async def test_prepare_not_found(conn):
    c, (d,) = _fede(conn, ("alias", "fede.mp"))
    with pytest.raises(ValidationError, match="no encontró"):
        await prepare_transfer(conn, FakeWallet(recipient=None), c.id, 200000, now=T0)
    assert conn.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 0
    assert not get_destination(conn, d.id).verified


async def test_prepare_routine_ttl(conn):
    c, _ = _fede(conn, ("alias", "fede.mp"))
    chat = await prepare_transfer(conn, FakeWallet(), c.id, 200000, now=T0)
    assert chat.expires_at == T0 + timedelta(minutes=10) and chat.routine_id is None
    routine = create_routine(conn, c.id, 200000, 1, hour=10, tz="America/Argentina/Buenos_Aires")
    a = await prepare_transfer(conn, FakeWallet(), c.id, 200000, routine_id=routine.id, now=T0)
    assert a.expires_at == T0 + timedelta(hours=24)
    assert a.routine_id == routine.id
    assert get_action(conn, a.id).routine_id == routine.id


async def test_prepare_without_browser_needs_verified_destination(conn):
    c, (d,) = _fede(conn, ("alias", "fede.mp"))
    with pytest.raises(ValidationError, match="agent-browser no está instalado"):
        await prepare_transfer(conn, FakeWallet(available=False), c.id, 200000, now=T0)
    set_verification(conn, d.id, FEDE_HOLDER, FEDE_CVU, "Brubank")
    a = await prepare_transfer(conn, FakeWallet(available=False), c.id, 200000, now=T0)
    assert a.kind == "transfer"


async def _claimed(conn, wallet):
    c, _ = _fede(conn, ("alias", "fede.mp"))
    a = await prepare_transfer(conn, wallet, c.id, 200000, now=T0)
    claim_transfer(conn, a.id, now=T0)
    return get_action(conn, a.id)


async def test_run_sent(conn):
    wallet = FakeWallet(recipient=FEDE)
    a = await _claimed(conn, wallet)
    report = await run_transfer(conn, wallet, a)
    assert get_action(conn, a.id).status == "enviada"
    assert report.text == "Listo. Fiwind aceptó la transferencia. Estado: En proceso."
    assert "enviada" in report.agent_note and FEDE_HOLDER not in report.agent_note
    assert wallet.calls[-1] == ("withdraw", WithdrawalOrder("fede.mp", FEDE_HOLDER, FEDE_CVU, 200000))


async def test_run_other_status_is_unconfirmed(conn):
    wallet = FakeWallet(recipient=FEDE, outcome=Sent("Rechazado"))
    a = await _claimed(conn, wallet)
    report = await run_transfer(conn, wallet, a)
    after = get_action(conn, a.id)
    assert (after.status, after.detail) == ("sin_confirmar", "otro estado")
    assert report.text == (
        "No sé si salió: Fiwind mostró el estado Rechazado. "
        "Revisá los movimientos de Fiwind antes de pedirla de nuevo."
    )
    assert report.agent_note == (
        "Resultado de la transferencia (Transferencia de $2.000 a Fede, alias fede.mp): "
        "sin confirmar, Fiwind mostró otro estado"
    )
    assert "Rechazado" not in report.agent_note


async def test_run_not_sent_offers_manual(conn):
    wallet = FakeWallet(recipient=FEDE, outcome=NotSent(STEP_AMOUNT, "no apareció el campo"))
    a = await _claimed(conn, wallet)
    report = await run_transfer(conn, wallet, a)
    done = get_action(conn, a.id)
    assert done.status == "no_salio" and done.detail == STEP_AMOUNT
    assert report.manual_action_id == a.id
    text = manual_instructions(conn, a)
    assert f"CVU: {FEDE_CVU}" in text and "Alias: fede.mp" in text
    assert f"Titular: {FEDE_HOLDER}" in text and "Monto: $2.000" in text
    assert FEDE_HOLDER not in report.agent_note and FEDE_CVU not in report.agent_note


async def test_run_mismatch_creates_update(conn):
    other = Recipient("Otra Persona", "Ualá", FEDE_CVU)
    wallet = FakeWallet(recipient=FEDE, outcome=Mismatch(other))
    a = await _claimed(conn, wallet)
    report = await run_transfer(conn, wallet, a)
    assert report.follow_up.kind == "update_verification"
    assert "Otra Persona" in report.follow_up.description
    assert "Otra Persona" not in agent_text(report.follow_up)
    assert get_action(conn, a.id).status == "no_salio"
    assert report.manual_action_id is None
    assert "Otra Persona" not in report.agent_note


async def test_run_expired(conn):
    wallet = FakeWallet(recipient=FEDE, outcome=Expired())
    a = await _claimed(conn, wallet)
    report = await run_transfer(conn, wallet, a)
    assert report.session_expired and report.manual_action_id == a.id
    assert get_action(conn, a.id).status == "no_salio"


async def test_run_unconfirmed(conn):
    wallet = FakeWallet(recipient=FEDE, outcome=Unconfirmed(STEP_SEND))
    a = await _claimed(conn, wallet)
    report = await run_transfer(conn, wallet, a)
    assert get_action(conn, a.id).status == "sin_confirmar"
    assert report.manual_action_id is None
    assert report.text.startswith("No sé si salió")


async def test_run_insufficient(conn):
    wallet = FakeWallet(recipient=FEDE, outcome=Insufficient(300))
    a = await _claimed(conn, wallet)
    report = await run_transfer(conn, wallet, a)
    assert "$3" in report.text and "Cargá Fiwind" in report.text
    assert report.manual_action_id is None
    assert get_action(conn, a.id).status == "no_salio"
