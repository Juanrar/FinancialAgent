import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from financial_agent.store import contacts
from financial_agent.store.db import NotFoundError, utc_now
from financial_agent.money import format_amount
from financial_agent.validation import DESTINATION_LABELS, ValidationError, account_label

PENDING_TTL = timedelta(minutes=10)
ROUTINE_TTL = timedelta(hours=24)
_RESTART_DETAIL = "la app se reinició durante el envío"
# Estados de una transferencia terminada, con el texto que ve Juan.
_HISTORY_LABELS = {
    "enviada": "enviada",
    "no_salio": "no salió",
    "sin_confirmar": "sin confirmar",
    "hecha_a_mano": "hecha a mano",
}

ActionKind = Literal[
    "delete_contact",
    "delete_destination",
    "update_destination_value",
    "set_primary",
    "transfer",
    "update_verification",
]
Outcome = Literal["confirmada", "cancelada", "vencida", "ya_resuelta", "fallida", "en_curso"]

_MSG_EXPIRED = "Esta acción venció."
_MSG_RESOLVED = "Esta acción ya se resolvió."
_MSG_MISSING = "No se pudo hacer: el contacto o destino ya no existe."
_MSG_CHANGED = "No se pudo hacer: los datos cambiaron desde que lo pediste. Pedilo de nuevo."


class _Changed(Exception):
    """La descripción actual ya no coincide con la que se mostró."""


class _Unverified(ValueError):
    """El destino de una transferencia no está verificado."""


@dataclass(frozen=True)
class PendingAction:
    id: int
    kind: str
    payload: dict
    description: str
    status: str
    created_at: datetime
    expires_at: datetime
    routine_id: int | None = None
    detail: str | None = None
    resolved_at: datetime | None = None


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
        row["routine_id"],
        row["detail"],
        datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
    )


def _get(conn: sqlite3.Connection, action_id: int) -> PendingAction:
    row = conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No hay una acción con id {action_id}.")
    return _from_row(row)


def get_action(conn: sqlite3.Connection, action_id: int) -> PendingAction:
    return _get(conn, action_id)


def agent_text(action: PendingAction) -> str:
    """Texto que puede ver Claude: nunca incluye datos leídos de Fiwind."""
    return action.payload.get("agent_text", action.description)


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
    if kind == "transfer":
        if not dest.verified:
            raise _Unverified("El destino no está verificado.")
        return (
            f"Transferir {format_amount(payload['amount_cents'])} a {name}: "
            f"{dest.holder}, {dest.bank}, {account_label(dest.account)} {dest.account}"
        )
    if kind == "update_verification":
        return (
            f"Actualizar el destino de {name} ({label} {dest.value}) a: "
            f"{payload['holder']}, {payload['bank']}, "
            f"{account_label(payload['account'])} {payload['account']}"
        )
    if kind == "delete_destination":
        return f"Borrar el {label} {dest.value} de {name}"
    if kind == "update_destination_value":
        return f"Cambiar el {label} de {name} de {dest.value} a {payload['value']}"
    if kind == "set_primary":
        return f"Marcar el {label} {dest.value} como destino principal de {name}"
    raise ValueError(f"Tipo de acción desconocido: {kind}")


def create_action(
    conn: sqlite3.Connection,
    kind: ActionKind,
    payload: dict,
    now: datetime | None = None,
    ttl: timedelta = PENDING_TTL,
    routine_id: int | None = None,
) -> PendingAction:
    now = now or utc_now()
    if kind == "transfer" and not payload.get("agent_text"):
        raise ValueError("Una transferencia necesita agent_text.")
    description = _describe(conn, kind, payload)
    if kind == "update_verification":
        dest = contacts.get_destination(conn, payload["destination_id"])
        name = contacts.get_contact(conn, dest.contact_id).name
        text = f"Actualizar los datos del destino de {name} ({DESTINATION_LABELS[dest.type]} {dest.value})"
        payload = {**payload, "agent_text": text}
    expires_at = now + ttl
    with conn:
        cur = conn.execute(
            "INSERT INTO pending_actions (kind, payload, description, status, created_at,"
            " expires_at, routine_id) VALUES (?, ?, ?, 'pendiente', ?, ?, ?)",
            (kind, json.dumps(payload), description, _iso(now), _iso(expires_at), routine_id),
        )
    return PendingAction(
        cur.lastrowid, kind, payload, description, "pendiente", now, expires_at, routine_id
    )


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
    elif action.kind == "update_verification":
        contacts._set_verification(
            conn, payload["destination_id"], payload["holder"], payload["account"], payload["bank"]
        )
    else:
        raise ValueError(f"Tipo de acción desconocido: {action.kind}")


def _not_applied(conn: sqlite3.Connection, action_id: int, now: datetime) -> Resolution:
    """Resuelve una acción que ya no está pendiente o que venció."""
    action = _get(conn, action_id)
    if action.status == "pendiente":
        conn.execute(
            "UPDATE pending_actions SET status = 'vencida', resolved_at = ? WHERE id = ?",
            (_iso(now), action_id),
        )
        return Resolution("vencida", _MSG_EXPIRED, _get(conn, action_id))
    if action.status == "vencida":
        return Resolution("vencida", _MSG_EXPIRED, action)
    return Resolution("ya_resuelta", _MSG_RESOLVED, action)


def confirm_action(
    conn: sqlite3.Connection, action_id: int, now: datetime | None = None
) -> Resolution:
    row = conn.execute("SELECT kind FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
    if row is not None and row["kind"] == "transfer":
        raise ValueError("Las transferencias se confirman con claim_transfer")
    now = now or utc_now()
    stamp = _iso(now)
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'confirmada', resolved_at = ?"
            " WHERE id = ? AND status = 'pendiente' AND expires_at > ?",
            (stamp, action_id, stamp),
        )
        if cur.rowcount == 0:
            return _not_applied(conn, action_id, now)
        action = _get(conn, action_id)
        try:
            if _describe(conn, action.kind, action.payload) != action.description:
                raise _Changed
            _execute(conn, action)
        except (_Changed, NotFoundError, ValidationError) as error:
            conn.execute(
                "UPDATE pending_actions SET status = 'cancelada', resolved_at = ? WHERE id = ?",
                (stamp, action_id),
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
            return _not_applied(conn, action_id, now)
    return Resolution("cancelada", "Cancelado.", _get(conn, action_id))


def claim_transfer(
    conn: sqlite3.Connection, action_id: int, now: datetime | None = None
) -> Resolution:
    """Reclama una transferencia para enviarla. No ejecuta nada: el envío lo hace quien llama."""
    now = now or utc_now()
    stamp = _iso(now)
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'en_curso'"
            " WHERE id = ? AND kind = 'transfer' AND status = 'pendiente' AND expires_at > ?",
            (action_id, stamp),
        )
        if cur.rowcount == 0:
            return _not_applied(conn, action_id, now)
        action = _get(conn, action_id)
        try:
            if _describe(conn, action.kind, action.payload) != action.description:
                raise _Changed
        except (_Changed, _Unverified, NotFoundError) as error:
            conn.execute(
                "UPDATE pending_actions SET status = 'cancelada', resolved_at = ? WHERE id = ?",
                (stamp, action_id),
            )
            message = _MSG_MISSING if isinstance(error, NotFoundError) else _MSG_CHANGED
            return Resolution("fallida", message, _get(conn, action_id))
    return Resolution("en_curso", "Enviando...", action)


def finish_transfer(
    conn: sqlite3.Connection,
    action_id: int,
    status: Literal["enviada", "no_salio", "sin_confirmar"],
    detail: str | None = None,
    now: datetime | None = None,
) -> PendingAction:
    now = now or utc_now()
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = ?, detail = ?, resolved_at = ?"
            " WHERE id = ? AND status = 'en_curso'",
            (status, detail, _iso(now), action_id),
        )
    if cur.rowcount == 0:
        raise ValueError("La transferencia no está en curso.")
    return _get(conn, action_id)


def mark_done_manually(
    conn: sqlite3.Connection, action_id: int, now: datetime | None = None
) -> bool:
    now = now or utc_now()
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'hecha_a_mano', resolved_at = ?"
            " WHERE id = ? AND status = 'no_salio'",
            (_iso(now), action_id),
        )
    return cur.rowcount == 1


def list_transfers(
    conn: sqlite3.Connection,
    *,
    contact_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[PendingAction]:
    """Transferencias terminadas, de la más nueva a la más vieja. Las pendientes,
    canceladas o vencidas no movieron plata y no aparecen."""
    marks = ", ".join("?" * len(_HISTORY_LABELS))
    sql = f"SELECT * FROM pending_actions WHERE kind = 'transfer' AND status IN ({marks})"
    params: list = list(_HISTORY_LABELS)
    if contact_id is not None:
        sql += (
            " AND json_extract(payload, '$.destination_id') IN"
            " (SELECT id FROM destinations WHERE contact_id = ?)"
        )
        params.append(contact_id)
    if since is not None:
        sql += " AND resolved_at >= ?"
        params.append(_iso(since))
    if until is not None:
        sql += " AND resolved_at < ?"
        params.append(_iso(until))
    sql += " ORDER BY resolved_at DESC, id DESC"
    return [_from_row(row) for row in conn.execute(sql, params).fetchall()]


def status_label(action: PendingAction) -> str:
    label = _HISTORY_LABELS.get(action.status, action.status)
    if action.status == "no_salio" and action.detail:
        return f"{label} ({action.detail})"
    return label


def expire_overdue_routine_actions(
    conn: sqlite3.Connection, now: datetime | None = None
) -> list[PendingAction]:
    now = now or utc_now()
    stamp = _iso(now)
    with conn:
        rows = conn.execute(
            "SELECT id FROM pending_actions WHERE status = 'pendiente'"
            " AND routine_id IS NOT NULL AND expires_at <= ?",
            (stamp,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE pending_actions SET status = 'vencida', resolved_at = ? WHERE id = ?",
                (stamp, row["id"]),
            )
    return [_get(conn, row["id"]) for row in rows]


def expire_all_pending(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Al arrancar: vence lo pendiente pedido por chat. Las confirmaciones de rutina
    (vencidas o no) quedan para el scheduler, que las vence y avisa."""
    now = now or utc_now()
    with conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'vencida', resolved_at = ?"
            " WHERE status = 'pendiente' AND routine_id IS NULL",
            (_iso(now),),
        )
    return cur.rowcount


def mark_in_flight_unconfirmed(
    conn: sqlite3.Connection, now: datetime | None = None
) -> list[PendingAction]:
    """Al arrancar: lo que quedó en curso pasa a sin confirmar y se devuelve para avisar."""
    now = now or utc_now()
    with conn:
        rows = conn.execute("SELECT id FROM pending_actions WHERE status = 'en_curso'").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE pending_actions SET status = 'sin_confirmar', detail = ?, resolved_at = ?"
                " WHERE id = ?",
                (_RESTART_DETAIL, _iso(now), row["id"]),
            )
    return [_get(conn, row["id"]) for row in rows]
