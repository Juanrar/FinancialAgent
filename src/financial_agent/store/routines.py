import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from financial_agent.money import format_amount
from financial_agent.store.contacts import get_contact, get_destination
from financial_agent.store.db import NotFoundError, utc_now
from financial_agent.validation import DESTINATION_LABELS, ValidationError

MIN_AMOUNT_CENTS = 100


@dataclass(frozen=True)
class Routine:
    id: int
    contact_id: int
    destination_id: int | None
    amount_cents: int
    day: int
    last_sent_month: str | None


def _from_row(row: sqlite3.Row) -> Routine:
    return Routine(
        row["id"],
        row["contact_id"],
        row["destination_id"],
        row["amount_cents"],
        row["day"],
        row["last_sent_month"],
    )


def effective_day(day: int, year: int, month: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])


def month_key(local: datetime) -> str:
    return f"{local.year:04d}-{local.month:02d}"


def _local(now: datetime, tz: str) -> datetime:
    return now.astimezone(ZoneInfo(tz))


def _moment_passed(day: int, local_now: datetime, hour: int) -> bool:
    moment = local_now.replace(
        day=effective_day(day, local_now.year, local_now.month),
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    return local_now >= moment


def next_run_date(routine: Routine, now: datetime, *, hour: int, tz: str) -> date:
    """Fecha local de la próxima vez que toca la rutina."""
    local_now = _local(now, tz)
    if routine.last_sent_month != month_key(local_now) and not _moment_passed(
        routine.day, local_now, hour
    ):
        year, month = local_now.year, local_now.month
    else:
        year, month = _following_month(local_now.year, local_now.month)
    return date(year, month, effective_day(routine.day, year, month))


def _following_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _initial_last_sent(day: int, now: datetime, hour: int, tz: str) -> str | None:
    local_now = _local(now, tz)
    return month_key(local_now) if _moment_passed(day, local_now, hour) else None


def _check_day(day: int) -> None:
    if not 1 <= day <= 31:
        raise ValidationError("El día tiene que estar entre 1 y 31.")


def _check_amount(amount_cents: int) -> None:
    if amount_cents < MIN_AMOUNT_CENTS:
        raise ValidationError("El monto mínimo es $1.")


def get_routine(conn: sqlite3.Connection, routine_id: int) -> Routine:
    row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No hay una rutina con id {routine_id}.")
    return _from_row(row)


def list_routines(conn: sqlite3.Connection) -> list[Routine]:
    rows = conn.execute("SELECT * FROM routines ORDER BY day, id").fetchall()
    return [_from_row(r) for r in rows]


def create_routine(
    conn: sqlite3.Connection,
    contact_id: int,
    amount_cents: int,
    day: int,
    *,
    hour: int,
    tz: str,
    destination_id: int | None = None,
    now: datetime | None = None,
) -> Routine:
    contact = get_contact(conn, contact_id)
    _check_day(day)
    _check_amount(amount_cents)
    if destination_id is not None:
        dest = get_destination(conn, destination_id)
        if dest.contact_id != contact_id:
            raise ValidationError(f"Ese destino no es de {contact.name}.")
        if dest.type == "email_mp":
            raise ValidationError(
                "Fiwind no transfiere a emails de Mercado Pago. Usá un CBU, CVU o alias."
            )
    now = now or utc_now()
    last_sent = _initial_last_sent(day, now, hour, tz)
    with conn:
        cur = conn.execute(
            "INSERT INTO routines (contact_id, destination_id, amount_cents, day,"
            " last_sent_month, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (contact_id, destination_id, amount_cents, day, last_sent, now.isoformat()),
        )
    return Routine(cur.lastrowid, contact_id, destination_id, amount_cents, day, last_sent)


def update_routine(
    conn: sqlite3.Connection,
    routine_id: int,
    *,
    hour: int,
    tz: str,
    amount_cents: int | None = None,
    day: int | None = None,
    now: datetime | None = None,
) -> Routine:
    routine = get_routine(conn, routine_id)
    if amount_cents is not None:
        _check_amount(amount_cents)
    if day is not None:
        _check_day(day)
    new_amount = routine.amount_cents if amount_cents is None else amount_cents
    new_day = routine.day if day is None else day
    last_sent = routine.last_sent_month
    if day is not None and day != routine.day:
        now = now or utc_now()
        if last_sent != month_key(_local(now, tz)):
            last_sent = _initial_last_sent(new_day, now, hour, tz)
    with conn:
        conn.execute(
            "UPDATE routines SET amount_cents = ?, day = ?, last_sent_month = ? WHERE id = ?",
            (new_amount, new_day, last_sent, routine_id),
        )
    return get_routine(conn, routine_id)


def delete_routine(conn: sqlite3.Connection, routine_id: int) -> Routine:
    routine = get_routine(conn, routine_id)
    with conn:
        conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
    return routine


def due_routines(
    conn: sqlite3.Connection, now: datetime, *, hour: int, tz: str
) -> list[Routine]:
    local_now = _local(now, tz)
    current = month_key(local_now)
    return [
        r
        for r in list_routines(conn)
        if r.last_sent_month != current and _moment_passed(r.day, local_now, hour)
    ]


def upcoming_routines(
    conn: sqlite3.Connection, now: datetime, *, days: int, tz: str
) -> list[tuple[Routine, date]]:
    """Rutinas sin mandar cuya fecha cae entre hoy y `days` días después, con su fecha.

    Incluye las de este mes que ya pasaron sin mandarse: salen en el próximo tick.
    """
    local_now = _local(now, tz)
    limit = local_now.date() + timedelta(days=days)
    found = []
    for routine in list_routines(conn):
        if routine.last_sent_month != month_key(local_now):
            year, month = local_now.year, local_now.month
        else:
            year, month = _following_month(local_now.year, local_now.month)
        when = date(year, month, effective_day(routine.day, year, month))
        if when <= limit:
            found.append((routine, when))
    return sorted(found, key=lambda item: item[1])


def mark_sent(conn: sqlite3.Connection, routine_id: int, month: str) -> None:
    get_routine(conn, routine_id)
    with conn:
        conn.execute("UPDATE routines SET last_sent_month = ? WHERE id = ?", (month, routine_id))


def monthly_total(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) AS total FROM routines").fetchone()
    return row["total"]


def describe_total(conn: sqlite3.Connection) -> str:
    return f"Total por mes: {format_amount(monthly_total(conn))}"


def describe_routine(conn: sqlite3.Connection, routine: Routine) -> str:
    contact = get_contact(conn, routine.contact_id)
    text = f"{format_amount(routine.amount_cents)} a {contact.name} los días {routine.day}"
    if routine.destination_id is not None:
        dest = get_destination(conn, routine.destination_id)
        text += f", {DESTINATION_LABELS[dest.type]} {dest.value}"
    return text
