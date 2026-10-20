from datetime import UTC, datetime, timedelta

import pytest

from financial_agent.store.contacts import (
    add_destination,
    create_contact,
    get_contact,
    get_destination,
)
from financial_agent.store.db import NotFoundError
from financial_agent.store.pending import (
    cancel_action,
    confirm_action,
    create_action,
    expire_all_pending,
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
