from datetime import UTC, datetime

import pytest

from financial_agent.store.db import NotFoundError
from financial_agent.store.memory import add_note, delete_note, list_notes, update_note
from financial_agent.validation import ValidationError


def test_notes_crud(conn):
    t0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
    n = add_note(conn, "  cobro el sueldo el día 5 ", now=t0)
    assert n.text == "cobro el sueldo el día 5" and n.updated_at == t0
    assert update_note(conn, n.id, "cobro el día 10").text == "cobro el día 10"
    assert [x.text for x in list_notes(conn)] == ["cobro el día 10"]
    assert delete_note(conn, n.id).text == "cobro el día 10"
    assert list_notes(conn) == []


def test_empty_note_rejected(conn):
    with pytest.raises(ValidationError):
        add_note(conn, "   ")


def test_missing_note_raises(conn):
    with pytest.raises(NotFoundError):
        update_note(conn, 99, "x")
    with pytest.raises(NotFoundError):
        delete_note(conn, 99)


def test_note_ids_are_not_reused_after_delete(conn):
    first = add_note(conn, "uno")
    second = add_note(conn, "dos")
    delete_note(conn, second.id)
    third = add_note(conn, "tres")
    assert third.id > max(first.id, second.id)
