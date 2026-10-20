import sqlite3
from dataclasses import dataclass

from financial_agent.store.db import NotFoundError, utc_now
from financial_agent.validation import ValidationError, normalize_text, validate_destination


@dataclass(frozen=True)
class Destination:
    id: int
    contact_id: int
    type: str
    value: str
    is_primary: bool


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    nicknames: tuple[str, ...]
    destinations: tuple[Destination, ...]


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise ValidationError("El nombre del contacto no puede estar vacío.")
    return name


def _clean_nickname(nickname: str) -> str:
    nickname = " ".join(nickname.split()).lower()
    if not nickname:
        raise ValidationError("El apodo no puede estar vacío.")
    return nickname


def _dest_from_row(row: sqlite3.Row) -> Destination:
    return Destination(
        row["id"], row["contact_id"], row["type"], row["value"], bool(row["is_primary"])
    )


def _contact_name(conn: sqlite3.Connection, contact_id: int) -> str:
    row = conn.execute("SELECT name FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No hay un contacto con id {contact_id}.")
    return row["name"]


def _contact_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Contact:
    nicks = conn.execute(
        "SELECT nickname FROM nicknames WHERE contact_id = ? ORDER BY nickname", (row["id"],)
    ).fetchall()
    dests = conn.execute(
        "SELECT * FROM destinations WHERE contact_id = ? ORDER BY id", (row["id"],)
    ).fetchall()
    return Contact(
        row["id"],
        row["name"],
        tuple(n["nickname"] for n in nicks),
        tuple(_dest_from_row(d) for d in dests),
    )


def get_contact(conn: sqlite3.Connection, contact_id: int) -> Contact:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No hay un contacto con id {contact_id}.")
    return _contact_from_row(conn, row)


def get_destination(conn: sqlite3.Connection, destination_id: int) -> Destination:
    row = conn.execute("SELECT * FROM destinations WHERE id = ?", (destination_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No hay un destino con id {destination_id}.")
    return _dest_from_row(row)


def create_contact(conn: sqlite3.Connection, name: str) -> Contact:
    name = _clean_name(name)
    with conn:
        cur = conn.execute(
            "INSERT INTO contacts (name, created_at) VALUES (?, ?)",
            (name, utc_now().isoformat()),
        )
    return Contact(cur.lastrowid, name, (), ())


def list_contacts(conn: sqlite3.Connection) -> list[Contact]:
    rows = conn.execute("SELECT * FROM contacts").fetchall()
    contacts = [_contact_from_row(conn, r) for r in rows]
    return sorted(contacts, key=lambda c: (normalize_text(c.name), c.id))


def search_contacts(conn: sqlite3.Connection, query: str) -> list[Contact]:
    needle = normalize_text(query)
    return [
        c
        for c in list_contacts(conn)
        if needle in normalize_text(c.name)
        or any(needle in normalize_text(n) for n in c.nicknames)
    ]


def rename_contact(conn: sqlite3.Connection, contact_id: int, name: str) -> Contact:
    name = _clean_name(name)
    _contact_name(conn, contact_id)
    with conn:
        conn.execute("UPDATE contacts SET name = ? WHERE id = ?", (name, contact_id))
    return get_contact(conn, contact_id)


def add_nickname(conn: sqlite3.Connection, contact_id: int, nickname: str) -> Contact:
    nickname = _clean_nickname(nickname)
    _contact_name(conn, contact_id)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO nicknames (contact_id, nickname) VALUES (?, ?)",
            (contact_id, nickname),
        )
    return get_contact(conn, contact_id)


def remove_nickname(conn: sqlite3.Connection, contact_id: int, nickname: str) -> Contact:
    nickname = " ".join(nickname.split()).lower()
    _contact_name(conn, contact_id)
    with conn:
        cur = conn.execute(
            "DELETE FROM nicknames WHERE contact_id = ? AND nickname = ?",
            (contact_id, nickname),
        )
    if cur.rowcount == 0:
        raise NotFoundError(f"El contacto no tiene el apodo '{nickname}'.")
    return get_contact(conn, contact_id)


def replace_nicknames(
    conn: sqlite3.Connection, contact_id: int, nicknames: list[str]
) -> Contact:
    cleaned = {_clean_nickname(n) for n in nicknames}
    _contact_name(conn, contact_id)
    with conn:
        conn.execute("DELETE FROM nicknames WHERE contact_id = ?", (contact_id,))
        conn.executemany(
            "INSERT INTO nicknames (contact_id, nickname) VALUES (?, ?)",
            [(contact_id, n) for n in sorted(cleaned)],
        )
    return get_contact(conn, contact_id)


def _delete_contact(conn: sqlite3.Connection, contact_id: int) -> None:
    _contact_name(conn, contact_id)
    conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))


def delete_contact(conn: sqlite3.Connection, contact_id: int) -> None:
    with conn:
        _delete_contact(conn, contact_id)


def add_destination(
    conn: sqlite3.Connection, contact_id: int, type_: str, value: str
) -> Destination:
    name = _contact_name(conn, contact_id)
    value = validate_destination(type_, value)
    duplicate = ValidationError(f"Ese destino ya está guardado para {name}.")
    with conn:
        exists = conn.execute(
            "SELECT 1 FROM destinations WHERE contact_id = ? AND type = ? AND value = ?",
            (contact_id, type_, value),
        ).fetchone()
        if exists:
            raise duplicate
        has_any = conn.execute(
            "SELECT 1 FROM destinations WHERE contact_id = ?", (contact_id,)
        ).fetchone()
        is_primary = has_any is None
        try:
            cur = conn.execute(
                "INSERT INTO destinations (contact_id, type, value, is_primary, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (contact_id, type_, value, int(is_primary), utc_now().isoformat()),
            )
        except sqlite3.IntegrityError:
            raise duplicate from None
    return Destination(cur.lastrowid, contact_id, type_, value, is_primary)


def _update_destination_value(
    conn: sqlite3.Connection, destination_id: int, value: str
) -> Destination:
    dest = get_destination(conn, destination_id)
    value = validate_destination(dest.type, value)
    name = _contact_name(conn, dest.contact_id)
    try:
        conn.execute("UPDATE destinations SET value = ? WHERE id = ?", (value, destination_id))
    except sqlite3.IntegrityError:
        raise ValidationError(f"Ese destino ya está guardado para {name}.") from None
    return get_destination(conn, destination_id)


def update_destination_value(
    conn: sqlite3.Connection, destination_id: int, value: str
) -> Destination:
    with conn:
        return _update_destination_value(conn, destination_id, value)


def _set_primary(conn: sqlite3.Connection, destination_id: int) -> Destination:
    dest = get_destination(conn, destination_id)
    conn.execute("UPDATE destinations SET is_primary = 0 WHERE contact_id = ?", (dest.contact_id,))
    conn.execute("UPDATE destinations SET is_primary = 1 WHERE id = ?", (destination_id,))
    return get_destination(conn, destination_id)


def set_primary(conn: sqlite3.Connection, destination_id: int) -> Destination:
    with conn:
        return _set_primary(conn, destination_id)


def _delete_destination(conn: sqlite3.Connection, destination_id: int) -> None:
    dest = get_destination(conn, destination_id)
    conn.execute("DELETE FROM destinations WHERE id = ?", (destination_id,))
    if not dest.is_primary:
        return
    remaining = conn.execute(
        "SELECT id FROM destinations WHERE contact_id = ?", (dest.contact_id,)
    ).fetchall()
    if len(remaining) == 1:
        conn.execute("UPDATE destinations SET is_primary = 1 WHERE id = ?", (remaining[0]["id"],))


def delete_destination(conn: sqlite3.Connection, destination_id: int) -> None:
    with conn:
        _delete_destination(conn, destination_id)
