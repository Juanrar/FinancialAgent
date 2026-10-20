import sqlite3
from dataclasses import dataclass
from datetime import datetime

from financial_agent.store.db import NotFoundError, utc_now
from financial_agent.validation import ValidationError


@dataclass(frozen=True)
class Note:
    id: int
    text: str
    updated_at: datetime


def _clean(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValidationError("La nota no puede estar vacía.")
    return text


def _from_row(row: sqlite3.Row) -> Note:
    return Note(row["id"], row["text"], datetime.fromisoformat(row["updated_at"]))


def _get(conn: sqlite3.Connection, note_id: int) -> Note:
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No hay una nota con id {note_id}.")
    return _from_row(row)


def add_note(conn: sqlite3.Connection, text: str, now: datetime | None = None) -> Note:
    text = _clean(text)
    now = now or utc_now()
    with conn:
        cur = conn.execute(
            "INSERT INTO notes (text, updated_at) VALUES (?, ?)", (text, now.isoformat())
        )
    return Note(cur.lastrowid, text, now)


def list_notes(conn: sqlite3.Connection) -> list[Note]:
    rows = conn.execute("SELECT * FROM notes ORDER BY id").fetchall()
    return [_from_row(r) for r in rows]


def update_note(
    conn: sqlite3.Connection, note_id: int, text: str, now: datetime | None = None
) -> Note:
    text = _clean(text)
    now = now or utc_now()
    _get(conn, note_id)
    with conn:
        conn.execute(
            "UPDATE notes SET text = ?, updated_at = ? WHERE id = ?",
            (text, now.isoformat(), note_id),
        )
    return Note(note_id, text, now)


def delete_note(conn: sqlite3.Connection, note_id: int) -> Note:
    note = _get(conn, note_id)
    with conn:
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return note
