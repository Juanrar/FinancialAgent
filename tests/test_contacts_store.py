import pytest

from financial_agent.store.contacts import (
    add_destination,
    add_nickname,
    create_contact,
    delete_contact,
    delete_destination,
    get_contact,
    get_destination,
    remove_nickname,
    rename_contact,
    replace_nicknames,
    search_contacts,
    set_primary,
    set_verification,
    update_destination_value,
)
from financial_agent.store.db import NotFoundError
from financial_agent.validation import ValidationError

CVU = "0000003100000000000017"
CBU = "2850590940090418135201"


def test_create_and_nicknames(conn):
    c = create_contact(conn, "Fede")
    c = add_nickname(conn, c.id, "  Mi   Hermano ")
    c = add_nickname(conn, c.id, "mi hermano")
    assert c.nicknames == ("mi hermano",)
    c = remove_nickname(conn, c.id, "mi hermano")
    assert c.nicknames == ()
    assert replace_nicknames(conn, c.id, ["fefe", "el fede"]).nicknames == ("el fede", "fefe")


def test_first_destination_is_primary(conn):
    c = create_contact(conn, "Fede")
    d1 = add_destination(conn, c.id, "cbu_cvu", CVU)
    d2 = add_destination(conn, c.id, "alias", "fede.mp")
    assert d1.is_primary and not d2.is_primary


def test_duplicate_destination_rejected(conn):
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    with pytest.raises(ValidationError):
        add_destination(conn, c.id, "alias", "FEDE.MP")


def test_set_primary_moves_mark(conn):
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "cbu_cvu", CVU)
    d2 = add_destination(conn, c.id, "alias", "fede.mp")
    set_primary(conn, d2.id)
    assert [d.is_primary for d in get_contact(conn, c.id).destinations] == [False, True]


def test_delete_primary_promotes_single_remaining(conn):
    c = create_contact(conn, "Fede")
    d1 = add_destination(conn, c.id, "cbu_cvu", CVU)
    d2 = add_destination(conn, c.id, "alias", "fede.mp")
    delete_destination(conn, d1.id)
    assert get_destination(conn, d2.id).is_primary


def test_delete_primary_with_several_leaves_none(conn):
    c = create_contact(conn, "Fede")
    d1 = add_destination(conn, c.id, "cbu_cvu", CVU)
    add_destination(conn, c.id, "alias", "fede.mp")
    add_destination(conn, c.id, "cbu_cvu", CBU)
    delete_destination(conn, d1.id)
    assert not any(d.is_primary for d in get_contact(conn, c.id).destinations)


def test_update_destination_value_validates(conn):
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    assert update_destination_value(conn, d.id, "Fede.Nuevo").value == "fede.nuevo"
    with pytest.raises(ValidationError):
        update_destination_value(conn, d.id, "x")


def test_update_destination_value_duplicate_rejected(conn):
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    d = add_destination(conn, c.id, "alias", "fede.otro")
    with pytest.raises(ValidationError):
        update_destination_value(conn, d.id, "FEDE.MP")


def test_search_ignores_case_and_accents(conn):
    m = create_contact(conn, "Martín")
    f = create_contact(conn, "Fede")
    add_nickname(conn, f.id, "mi hermano")
    add_nickname(conn, m.id, "Mi Hermano")
    assert [c.name for c in search_contacts(conn, "martin")] == ["Martín"]
    assert {c.name for c in search_contacts(conn, "MI HERMANO")} == {"Fede", "Martín"}


def test_delete_contact_cascades(conn):
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    delete_contact(conn, c.id)
    with pytest.raises(NotFoundError):
        get_destination(conn, d.id)
    with pytest.raises(NotFoundError):
        get_contact(conn, c.id)


def test_empty_name_rejected(conn):
    with pytest.raises(ValidationError):
        create_contact(conn, "   ")
    c = create_contact(conn, "Fede")
    with pytest.raises(ValidationError):
        rename_contact(conn, c.id, "")


def test_missing_ids_raise_not_found(conn):
    c = create_contact(conn, "Fede")
    with pytest.raises(NotFoundError):
        rename_contact(conn, 99, "X")
    with pytest.raises(NotFoundError):
        add_destination(conn, 99, "alias", "fede.mp")
    with pytest.raises(NotFoundError):
        remove_nickname(conn, c.id, "nada")
    with pytest.raises(NotFoundError):
        delete_destination(conn, 99)
    with pytest.raises(NotFoundError):
        set_primary(conn, 99)
    with pytest.raises(NotFoundError):
        delete_contact(conn, 99)


def test_set_verification(conn):
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    assert not d.verified
    v = set_verification(conn, d.id, "Federico Pérez", "0000168300000000123459", "Brubank")
    assert (v.holder, v.account, v.bank, v.verified) == (
        "Federico Pérez", "0000168300000000123459", "Brubank", True)
    assert get_destination(conn, d.id) == v


def test_update_value_clears_verification(conn):
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    set_verification(conn, d.id, "Federico Pérez", "0000168300000000123459", "Brubank")
    update_destination_value(conn, d.id, "fede.nuevo")
    after = get_destination(conn, d.id)
    assert after.verified is False
    assert (after.holder, after.account, after.bank) == (None, None, None)
    assert conn.execute(
        "SELECT verified_at FROM destinations WHERE id = ?", (d.id,)
    ).fetchone()[0] is None
