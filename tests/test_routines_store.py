from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from financial_agent.store.contacts import (
    add_destination,
    create_contact,
    delete_contact,
    delete_destination,
    get_contact,
)
from financial_agent.store.db import NotFoundError
from financial_agent.store.routines import (
    create_routine,
    delete_routine,
    describe_routine,
    due_routines,
    effective_day,
    get_routine,
    list_routines,
    mark_sent,
    monthly_total,
    next_run_date,
    update_routine,
)
from financial_agent.validation import ValidationError

TZ = "America/Argentina/Buenos_Aires"


def local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ZoneInfo(TZ))


def _tio(conn):
    c = create_contact(conn, "Tío Pepe")
    return c, add_destination(conn, c.id, "alias", "tio.pepe")


def _create(conn, day, now, **kw):
    c, _ = _tio(conn)
    return create_routine(conn, c.id, 3000000, day, hour=10, tz=TZ, now=now, **kw)


def test_effective_day():
    assert effective_day(31, 2026, 2) == 28
    assert effective_day(31, 2028, 2) == 29
    assert effective_day(31, 2026, 4) == 30
    assert effective_day(5, 2026, 4) == 5


def test_created_after_its_day_starts_next_month(conn):
    r = _create(conn, 5, local(2026, 10, 20, 12))
    assert r.last_sent_month == "2026-10"
    assert due_routines(conn, local(2026, 10, 25, 12), hour=10, tz=TZ) == []
    assert due_routines(conn, local(2026, 11, 5, 10), hour=10, tz=TZ) == [r]


def test_created_same_day_before_hour(conn):
    r = _create(conn, 5, local(2026, 11, 5, 9))
    assert r.last_sent_month is None
    assert due_routines(conn, local(2026, 11, 5, 9, 59), hour=10, tz=TZ) == []
    assert due_routines(conn, local(2026, 11, 5, 10), hour=10, tz=TZ) == [r]


def test_missed_days_send_once(conn):
    r = _create(conn, 5, local(2026, 10, 1, 12))
    assert due_routines(conn, local(2026, 11, 8, 15), hour=10, tz=TZ) == [r]
    mark_sent(conn, r.id, "2026-11")
    assert due_routines(conn, local(2026, 11, 8, 15, 1), hour=10, tz=TZ) == []


def test_day_31_in_30_day_month(conn):
    r = _create(conn, 31, local(2026, 10, 31, 12))
    assert due_routines(conn, local(2026, 11, 30, 10), hour=10, tz=TZ) == [r]


def test_due_converts_utc_to_local_month(conn):
    r = _create(conn, 1, local(2026, 10, 15, 12))
    utc = ZoneInfo("UTC")
    # 2026-11-01 02:00 UTC es 31 de octubre 23:00 en Argentina
    assert due_routines(conn, datetime(2026, 11, 1, 2, tzinfo=utc), hour=10, tz=TZ) == []
    assert due_routines(conn, datetime(2026, 11, 1, 13, tzinfo=utc), hour=10, tz=TZ) == [r]


def test_due_and_list_order_by_day_then_id(conn):
    c, _ = _tio(conn)
    now = local(2026, 10, 1, 12)
    a = create_routine(conn, c.id, 100000, 20, hour=10, tz=TZ, now=now)
    b = create_routine(conn, c.id, 100000, 5, hour=10, tz=TZ, now=now)
    d = create_routine(conn, c.id, 100000, 5, hour=10, tz=TZ, now=now)
    assert list_routines(conn) == [b, d, a]
    assert due_routines(conn, local(2026, 11, 25, 12), hour=10, tz=TZ) == [b, d, a]


def test_create_validations(conn):
    c, d = _tio(conn)
    other = create_contact(conn, "Ana")
    email = add_destination(conn, other.id, "email_mp", "ana@mail.com")
    for kwargs, exc in [
        ({"day": 0}, ValidationError),
        ({"day": 32}, ValidationError),
        ({"amount_cents": 99}, ValidationError),
        ({"destination_id": email.id}, ValidationError),
    ]:
        args = {"amount_cents": 3000000, "day": 5} | kwargs
        dest = args.pop("destination_id", None)
        with pytest.raises(exc):
            create_routine(
                conn, c.id, args["amount_cents"], args["day"], hour=10, tz=TZ,
                destination_id=dest, now=local(2026, 10, 1, 12),
            )
    with pytest.raises(NotFoundError):
        create_routine(conn, 999, 3000000, 5, hour=10, tz=TZ)


def test_create_validation_messages(conn):
    c, _ = _tio(conn)
    other = create_contact(conn, "Ana")
    email = add_destination(conn, other.id, "email_mp", "ana@mail.com")
    cbu = add_destination(conn, other.id, "cbu_cvu", "0000003100000000000017")
    now = local(2026, 10, 1, 12)

    def fail(*args, **kw):
        with pytest.raises(ValidationError) as e:
            create_routine(conn, *args, hour=10, tz=TZ, now=now, **kw)
        return str(e.value)

    assert fail(c.id, 3000000, 0) == "El día tiene que estar entre 1 y 31."
    assert fail(c.id, 99, 5) == "El monto mínimo es $1."
    assert fail(c.id, 3000000, 5, destination_id=cbu.id) == "Ese destino no es de Tío Pepe."
    assert fail(other.id, 3000000, 5, destination_id=email.id) == (
        "Fiwind no transfiere a emails de Mercado Pago. Usá un CBU, CVU o alias."
    )


def test_update_day_recomputes_when_not_sent(conn):
    r = _create(conn, 25, local(2026, 10, 20, 12))
    assert r.last_sent_month is None
    r2 = update_routine(conn, r.id, hour=10, tz=TZ, day=5, now=local(2026, 10, 21, 12))
    assert r2.last_sent_month == "2026-10"
    assert get_routine(conn, r.id) == r2


def test_update_amount_only_and_validations(conn):
    r = _create(conn, 25, local(2026, 10, 20, 12))
    r2 = update_routine(conn, r.id, hour=10, tz=TZ, amount_cents=500000)
    assert r2.amount_cents == 500000
    assert r2.day == 25
    assert r2.last_sent_month is None
    with pytest.raises(ValidationError):
        update_routine(conn, r.id, hour=10, tz=TZ, day=40)
    with pytest.raises(ValidationError):
        update_routine(conn, r.id, hour=10, tz=TZ, amount_cents=50)
    with pytest.raises(NotFoundError):
        update_routine(conn, 999, hour=10, tz=TZ, day=3)


def test_update_day_keeps_sent_month_when_already_sent_this_month(conn):
    r = _create(conn, 5, local(2026, 10, 1, 12))
    mark_sent(conn, r.id, "2026-10")
    r2 = update_routine(conn, r.id, hour=10, tz=TZ, day=28, now=local(2026, 10, 10, 12))
    assert r2.last_sent_month == "2026-10"


def test_describe_and_total(conn):
    r = _create(conn, 5, local(2026, 10, 1, 12))
    assert describe_routine(conn, r) == "$30.000 a Tío Pepe los días 5"
    d = get_contact(conn, r.contact_id).destinations[0]
    r2 = create_routine(
        conn, r.contact_id, 1500000, 20, hour=10, tz=TZ, destination_id=d.id,
        now=local(2026, 10, 1, 12),
    )
    assert describe_routine(conn, r2) == "$15.000 a Tío Pepe los días 20, alias tio.pepe"
    assert monthly_total(conn) == 4500000


def test_describe_cbu(conn):
    c = create_contact(conn, "Ana")
    d = add_destination(conn, c.id, "cbu_cvu", "0000003100000000000017")
    r = create_routine(conn, c.id, 100000, 3, hour=10, tz=TZ, destination_id=d.id,
                       now=local(2026, 10, 1, 12))
    assert describe_routine(conn, r) == "$1.000 a Ana los días 3, CBU/CVU 0000003100000000000017"


def test_monthly_total_empty(conn):
    assert monthly_total(conn) == 0


def test_delete_routine(conn):
    r = _create(conn, 5, local(2026, 10, 1, 12))
    assert delete_routine(conn, r.id) == r
    with pytest.raises(NotFoundError):
        get_routine(conn, r.id)
    with pytest.raises(NotFoundError):
        delete_routine(conn, r.id)


def test_cascades(conn):
    c, d = _tio(conn)
    r = create_routine(conn, c.id, 3000000, 5, hour=10, tz=TZ, destination_id=d.id,
                       now=local(2026, 10, 1, 12))
    delete_destination(conn, d.id)
    assert get_routine(conn, r.id).destination_id is None
    delete_contact(conn, c.id)
    assert list_routines(conn) == []


def _r(conn, day, now):
    c = create_contact(conn, "Tío Pepe")
    return create_routine(conn, c.id, 100000, day, hour=10, tz=TZ, now=now)


def test_next_run_date(conn):
    base = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo(TZ))
    pending_day = _r(conn, 25, base)
    assert next_run_date(pending_day, base, hour=10, tz=TZ) == date(2026, 10, 25)
    passed = _r(conn, 5, base)  # nace enviada: la próxima es el mes siguiente
    assert next_run_date(passed, base, hour=10, tz=TZ) == date(2026, 11, 5)
    early = _r(conn, 20, datetime(2026, 10, 20, 9, tzinfo=ZoneInfo(TZ)))
    assert next_run_date(early, datetime(2026, 10, 20, 9, tzinfo=ZoneInfo(TZ)),
                         hour=10, tz=TZ) == date(2026, 10, 20)


def test_next_run_date_year_end_and_short_month(conn):
    dec = datetime(2026, 12, 20, 12, tzinfo=ZoneInfo(TZ))
    assert next_run_date(_r(conn, 5, dec), dec, hour=10, tz=TZ) == date(2027, 1, 5)
    oct_ = datetime(2026, 10, 31, 12, tzinfo=ZoneInfo(TZ))
    assert next_run_date(_r(conn, 31, oct_), oct_, hour=10, tz=TZ) == date(2026, 11, 30)
