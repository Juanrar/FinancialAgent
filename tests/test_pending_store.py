from datetime import UTC, datetime, timedelta

import pytest

from financial_agent.store.contacts import (
    add_destination,
    create_contact,
    get_contact,
    get_destination,
    set_verification,
    update_destination_value,
)
from financial_agent.store.db import NotFoundError
from financial_agent.store.pending import (
    ROUTINE_TTL,
    agent_text,
    cancel_action,
    claim_transfer,
    confirm_action,
    create_action,
    expire_all_pending,
    expire_overdue_routine_actions,
    finish_transfer,
    get_action,
    mark_done_manually,
)

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)


def _fede(conn):
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    return c, d


def test_create_builds_description_and_does_not_execute(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    assert a.description == "Borrar a Fede y su destino"
    assert a.status == "pendiente" and a.expires_at == T0 + timedelta(minutes=10)
    assert get_contact(conn, c.id).name == "Fede"


def test_delete_contact_descriptions(conn):
    c, d = _fede(conn)
    add_destination(conn, c.id, "cbu_cvu", "0000003100000000000017")
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    assert a.description == "Borrar a Fede y sus 2 destinos"
    empty = create_contact(conn, "Ana")
    b = create_action(conn, "delete_contact", {"contact_id": empty.id}, now=T0)
    assert b.description == "Borrar a Ana"


def test_confirm_executes_once(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_destination", {"destination_id": d.id}, now=T0)
    r1 = confirm_action(conn, a.id, now=T0 + timedelta(minutes=1))
    r2 = confirm_action(conn, a.id, now=T0 + timedelta(minutes=1))
    assert r1.outcome == "confirmada" and r1.message == "Listo: Borrar el alias fede.mp de Fede."
    assert r1.action.status == "confirmada"
    assert r2.outcome == "ya_resuelta"
    assert get_contact(conn, c.id).destinations == ()


def test_cancel(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    r = cancel_action(conn, a.id, now=T0)
    assert r.outcome == "cancelada" and r.message == "Cancelado."
    assert confirm_action(conn, a.id, now=T0).outcome == "ya_resuelta"
    assert get_contact(conn, c.id)


def test_cancel_after_ttl_is_expired(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    r = cancel_action(conn, a.id, now=T0 + timedelta(minutes=11))
    assert r.outcome == "vencida"


def test_expired_after_ttl(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    r = confirm_action(conn, a.id, now=T0 + timedelta(minutes=10, seconds=1))
    assert r.outcome == "vencida" and r.message == "Esta acción venció."
    assert r.action.status == "vencida"
    assert get_contact(conn, c.id)


def test_restart_expires_pending(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    assert expire_all_pending(conn, now=T0) == 1
    assert confirm_action(conn, a.id, now=T0).outcome == "vencida"


def test_update_value_and_set_primary(conn):
    c, d = _fede(conn)
    d2 = add_destination(conn, c.id, "cbu_cvu", "0000003100000000000017")
    a = create_action(
        conn, "update_destination_value", {"destination_id": d.id, "value": "fede.nuevo"}, now=T0
    )
    assert a.description == "Cambiar el alias de Fede de fede.mp a fede.nuevo"
    b = create_action(conn, "set_primary", {"destination_id": d2.id}, now=T0)
    assert b.description == (
        "Marcar el CBU/CVU 0000003100000000000017 como destino principal de Fede"
    )
    confirm_action(conn, a.id, now=T0)
    confirm_action(conn, b.id, now=T0)
    assert get_destination(conn, d.id).value == "fede.nuevo"
    assert get_destination(conn, d2.id).is_primary


def test_confirm_when_target_already_deleted(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_destination", {"destination_id": d.id}, now=T0)
    b = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    confirm_action(conn, b.id, now=T0)
    r = confirm_action(conn, a.id, now=T0)
    assert r.outcome == "fallida"
    assert r.message == "No se pudo hacer: el contacto o destino ya no existe."
    assert r.action.status == "cancelada"
    assert confirm_action(conn, a.id, now=T0).outcome == "ya_resuelta"


def test_confirm_with_validation_error_is_failed(conn):
    c, d = _fede(conn)
    a = create_action(
        conn, "update_destination_value", {"destination_id": d.id, "value": "fede.nuevo"}, now=T0
    )
    add_destination(conn, c.id, "alias", "fede.nuevo")
    r = confirm_action(conn, a.id, now=T0)
    assert r.outcome == "fallida"
    assert r.message == "No se pudo hacer: Ese destino ya está guardado para Fede."
    assert r.action.status == "cancelada"
    assert get_destination(conn, d.id).value == "fede.mp"


def test_create_for_missing_target(conn):
    with pytest.raises(NotFoundError):
        create_action(conn, "delete_contact", {"contact_id": 99}, now=T0)


def test_confirm_fails_if_destinations_changed(conn):
    c, d = _fede(conn)
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    add_destination(conn, c.id, "cbu_cvu", "0000003100000000000017")
    r = confirm_action(conn, a.id, now=T0)
    assert r.outcome == "fallida"
    assert r.message == (
        "No se pudo hacer: los datos cambiaron desde que lo pediste. Pedilo de nuevo."
    )
    assert r.action.status == "cancelada"
    assert len(get_contact(conn, c.id).destinations) == 2


def test_confirm_fails_if_old_value_changed(conn):
    c, d = _fede(conn)
    a = create_action(
        conn, "update_destination_value", {"destination_id": d.id, "value": "fede.nuevo"}, now=T0
    )
    update_destination_value(conn, d.id, "fede.otro")
    r = confirm_action(conn, a.id, now=T0)
    assert r.outcome == "fallida"
    assert r.message.endswith("Pedilo de nuevo.")
    assert get_destination(conn, d.id).value == "fede.otro"


def test_expired_action_gets_resolved_at(conn):
    c, _ = _fede(conn)
    a = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    later = T0 + timedelta(minutes=11)
    assert confirm_action(conn, a.id, now=later).outcome == "vencida"
    row = conn.execute("SELECT resolved_at FROM pending_actions WHERE id = ?", (a.id,)).fetchone()
    assert datetime.fromisoformat(row["resolved_at"]) == later


CVU = "0000168300000000123459"


def _verified(conn):
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    set_verification(conn, d.id, "Federico Pérez", CVU, "Brubank")
    return c, d


def _transfer(conn, now=T0, **kw):
    c, d = _verified(conn)
    payload = {"contact_id": c.id, "destination_id": d.id, "amount_cents": 200000,
               "agent_text": "Transferencia de $2.000 a Fede, alias fede.mp"}
    return create_action(conn, "transfer", payload, now=now, **kw), d


def test_transfer_description(conn):
    a, _ = _transfer(conn)
    assert a.description == f"Transferir $2.000 a Fede: Federico Pérez, Brubank, CVU {CVU}"


def test_agent_text_hides_fiwind_data(conn):
    a, _ = _transfer(conn)
    assert agent_text(a) == "Transferencia de $2.000 a Fede, alias fede.mp"
    for secret in ("Federico", "Brubank", CVU):
        assert secret not in agent_text(a)
    c = create_contact(conn, "Ana")
    old = create_action(conn, "delete_contact", {"contact_id": c.id}, now=T0)
    assert agent_text(old) == old.description


def test_claim_transfer_once(conn):
    a, _ = _transfer(conn)
    assert claim_transfer(conn, a.id, now=T0).outcome == "en_curso"
    assert get_action(conn, a.id).status == "en_curso"
    assert claim_transfer(conn, a.id, now=T0).outcome == "ya_resuelta"


def test_claim_transfer_expired(conn):
    a, _ = _transfer(conn)
    assert claim_transfer(conn, a.id, now=T0 + timedelta(minutes=11)).outcome == "vencida"


def test_claim_after_destination_changed(conn):
    a, d = _transfer(conn)
    update_destination_value(conn, d.id, "fede.nuevo")
    r = claim_transfer(conn, a.id, now=T0)
    assert r.outcome == "fallida" and get_action(conn, a.id).status == "cancelada"


def test_confirm_action_rejects_transfer(conn):
    a, _ = _transfer(conn)
    with pytest.raises(ValueError):
        confirm_action(conn, a.id, now=T0)


def test_finish_transfer(conn):
    a, _ = _transfer(conn)
    with pytest.raises(ValueError):
        finish_transfer(conn, a.id, "enviada", now=T0)
    claim_transfer(conn, a.id, now=T0)
    done = finish_transfer(conn, a.id, "no_salio", "buscar el destino", now=T0)
    assert (done.status, done.detail) == ("no_salio", "buscar el destino")


def test_mark_done_manually(conn):
    a, _ = _transfer(conn)
    claim_transfer(conn, a.id, now=T0)
    finish_transfer(conn, a.id, "no_salio", now=T0)
    assert mark_done_manually(conn, a.id, now=T0) is True
    assert get_action(conn, a.id).status == "hecha_a_mano"
    assert mark_done_manually(conn, a.id, now=T0) is False


def _routine_id(conn, contact_id):
    return conn.execute("INSERT INTO routines (contact_id, amount_cents, day, created_at)"
                        " VALUES (?, 200000, 5, 'x')", (contact_id,)).lastrowid


def test_expire_all_pending_keeps_live_routine_actions(conn):
    adhoc, d = _transfer(conn)
    rid = _routine_id(conn, d.contact_id)
    payload = {**adhoc.payload}
    live = create_action(conn, "transfer", payload, now=T0, ttl=ROUTINE_TTL, routine_id=rid)
    old = create_action(conn, "transfer", payload, now=T0 - timedelta(days=2),
                        ttl=ROUTINE_TTL, routine_id=rid)
    expire_all_pending(conn, now=T0 + timedelta(hours=1))
    assert [get_action(conn, x.id).status for x in (adhoc, live, old)] == [
        "vencida", "pendiente", "vencida"]


def test_expire_overdue_routine_actions(conn):
    a, d = _transfer(conn)
    rid = _routine_id(conn, d.contact_id)
    r = create_action(conn, "transfer", a.payload, now=T0, ttl=ROUTINE_TTL, routine_id=rid)
    assert expire_overdue_routine_actions(conn, now=T0 + timedelta(hours=23)) == []
    expired = expire_overdue_routine_actions(conn, now=T0 + timedelta(hours=25))
    assert [x.id for x in expired] == [r.id] and get_action(conn, r.id).status == "vencida"
    assert get_action(conn, a.id).status == "pendiente"  # las pedidas por chat no se tocan
    assert expire_overdue_routine_actions(conn, now=T0 + timedelta(hours=26)) == []


def test_expire_all_pending_marks_in_flight_unconfirmed(conn):
    a, _ = _transfer(conn)
    claim_transfer(conn, a.id, now=T0)
    expire_all_pending(conn, now=T0)
    after = get_action(conn, a.id)
    assert (after.status, after.detail) == ("sin_confirmar", "la app se reinició durante el envío")


def test_update_verification_action(conn):
    c, d = _verified(conn)
    a = create_action(conn, "update_verification", {"destination_id": d.id,
        "holder": "Federico J. Pérez", "account": CVU, "bank": "Ualá"}, now=T0)
    assert a.description == (f"Actualizar el destino de Fede (alias fede.mp) a: "
                             f"Federico J. Pérez, Ualá, CVU {CVU}")
    assert confirm_action(conn, a.id, now=T0).outcome == "confirmada"
    assert get_destination(conn, d.id).holder == "Federico J. Pérez"


def test_transfer_requires_agent_text(conn):
    c, d = _verified(conn)
    with pytest.raises(ValueError):
        create_action(conn, "transfer", {"contact_id": c.id, "destination_id": d.id,
                                         "amount_cents": 200000}, now=T0)


def test_update_verification_agent_text_has_no_fiwind_data(conn):
    c, d = _verified(conn)
    a = create_action(conn, "update_verification", {"destination_id": d.id,
        "holder": "Federico J. Pérez", "account": CVU, "bank": "Ualá"}, now=T0)
    assert agent_text(a) == "Actualizar los datos del destino de Fede (alias fede.mp)"
    for secret in ("Federico", "Ualá", CVU):
        assert secret not in agent_text(a)
    assert agent_text(get_action(conn, a.id)) == agent_text(a)
