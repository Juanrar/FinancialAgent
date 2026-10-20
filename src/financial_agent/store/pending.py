import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from financial_agent.store import contacts
from financial_agent.store.db import NotFoundError, utc_now
from financial_agent.validation import DESTINATION_LABELS, ValidationError

PENDING_TTL = timedelta(minutes=10)

ActionKind = Literal[
    "delete_contact", "delete_destination", "update_destination_value", "set_primary"
]
Outcome = Literal["confirmada", "cancelada", "vencida", "ya_resuelta", "fallida"]

_MSG_EXPIRED = "Esta acción venció."
_MSG_RESOLVED = "Esta acción ya se resolvió."
_MSG_MISSING = "No se pudo hacer: el contacto o destino ya no existe."
_MSG_CHANGED = "No se pudo hacer: los datos cambiaron desde que lo pediste. Pedilo de nuevo."


class _Changed(Exception):
    """La descripción actual ya no coincide con la que se mostró."""


@dataclass(frozen=True)
class PendingAction:
    id: int
    kind: str
    payload: dict
    description: str
    status: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class Resolution:
    outcome: Outcome
    message: str
    action: PendingAction


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _from_row(row: sqlite3.Row) -> PendingAction:
    return PendingAction(
        row["id"],
        row["kind"],
        json.loads(row["payload"]),
        row["description"],
        row["status"],
        datetime.fromisoformat(row["created_at"]),
        datetime.fromisoformat(row["expires_at"]),
    )


def _get(conn: sqlite3.Connection, action_id: int) -> PendingAction:
    row = conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No hay una acción con id {action_id}.")
    return _from_row(row)


def _describe(conn: sqlite3.Connection, kind: str, payload: dict) -> str:
    if kind == "delete_contact":
        contact = contacts.get_contact(conn, payload["contact_id"])
        count = len(contact.destinations)
        if count == 0:
            return f"Borrar a {contact.name}"
        if count == 1:
            return f"Borrar a {contact.name} y su destino"
        return f"Borrar a {contact.name} y sus {count} destinos"
    dest = contacts.get_destination(conn, payload["destination_id"])
    name = contacts.get_contact(conn, dest.contact_id).name
    label = DESTINATION_LABELS[dest.type]
    if kind == "delete_destination":
        return f"Borrar el {label} {dest.value} de {name}"
    if kind == "update_destination_value":
        return f"Cambiar el {label} de {name} de {dest.value} a {payload['value']}"
    if kind == "set_primary":
        return f"Marcar el {label} {dest.value} como destino principal de {name}"
    raise ValueError(f"Tipo de acción desconocido: {kind}")


def create_action(
    conn: sqlite3.Connection, kind: ActionKind, payload: dict, now: datetime | None = None
) -> PendingAction:
    now = now or utc_now()
    description = _describe(conn, kind, payload)
    expires_at = now + PENDING_TTL
    with conn:
        cur = conn.execute(
            "INSERT INTO pending_actions (kind, payload, description, status, created_at,"
            " expires_at) VALUES (?, ?, ?, 'pendiente', ?, ?)",
            (kind, json.dumps(payload), description, _iso(now), _iso(expires_at)),
        )
    return PendingAction(cur.lastrowid, kind, payload, description, "pendiente", now, expires_at)


def _execute(conn: sqlite3.Connection, action: PendingAction) -> None:
    payload = action.payload
    if action.kind == "delete_contact":
        contacts._delete_contact(conn, payload["contact_id"])
    elif action.kind == "delete_destination":
        contacts._delete_destination(conn, payload["destination_id"])
    elif action.kind == "update_destination_value":
        contacts._update_destination_value(conn, payload["destination_id"], payload["value"])
    elif action.kind == "set_primary":
        contacts._set_primary(conn, payload["destination_id"])
    else:
        raise ValueError(f"Tipo de acción desconocido: {action.kind}")


def _not_applied(conn: sqlite3.Connection, action_id: int) -> Resolution:
    """Resuelve una acción que ya no está pendiente o que venció."""
    action = _get(conn, action_id)
    if action.status == "pendiente":
        conn.execute("UPDATE pending_actions SET status = 'vencida' WHERE id = ?", (action_id,))
        return Resolution("vencida", _MSG_EXPIRED, _get(conn, action_id))
    if action.status == "vencida":
        return Resolution("vencida", _MSG_EXPIRED, action)
    return Resolution("ya_resuelta", _MSG_RESOLVED, action)


def confirm_action(
    conn: sqlite3.Connection, action_id: int, now: datetime | None = None
) -> Resolution:
    now = now or utc_now()
    stamp = _iso(now)
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'confirmada', resolved_at = ?"
            " WHERE id = ? AND status = 'pendiente' AND expires_at > ?",
            (stamp, action_id, stamp),
        )
        if cur.rowcount == 0:
            return _not_applied(conn, action_id)
        action = _get(conn, action_id)
        try:
            if _describe(conn, action.kind, action.payload) != action.description:
                raise _Changed
            _execute(conn, action)
        except (_Changed, NotFoundError, ValidationError) as error:
            conn.execute(
                "UPDATE pending_actions SET status = 'cancelada' WHERE id = ?", (action_id,)
            )
            if isinstance(error, NotFoundError):
                message = _MSG_MISSING
            elif isinstance(error, _Changed):
                message = _MSG_CHANGED
            else:
                message = f"No se pudo hacer: {error}"
            return Resolution("fallida", message, _get(conn, action_id))
    return Resolution("confirmada", f"Listo: {action.description}.", action)


def cancel_action(
    conn: sqlite3.Connection, action_id: int, now: datetime | None = None
) -> Resolution:
    now = now or utc_now()
    stamp = _iso(now)
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'cancelada', resolved_at = ?"
            " WHERE id = ? AND status = 'pendiente' AND expires_at > ?",
            (stamp, action_id, stamp),
        )
        if cur.rowcount == 0:
            return _not_applied(conn, action_id)
    return Resolution("cancelada", "Cancelado.", _get(conn, action_id))


def expire_all_pending(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    now = now or utc_now()
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'vencida', resolved_at = ?"
            " WHERE status = 'pendiente'",
            (_iso(now),),
        )
    return cur.rowcount
