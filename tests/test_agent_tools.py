from financial_agent.agent.tools import (
    ALLOWED_TOOLS,
    TOOL_NAMES,
    TurnState,
    build_server,
    build_tools,
)
from fakes import FakeWallet

from financial_agent.fiwind.client import FiwindUnavailable, SessionExpired
from financial_agent.fiwind.pages import Recipient
from financial_agent.store.contacts import add_destination, create_contact, get_contact
from financial_agent.store.memory import list_notes

_RECIPIENT = Recipient("Federico Pérez", "Brubank", "0000168300000000123459")


def _tools(conn, wallet=None):
    turn = TurnState(notices=[], pending=[])
    wallet = wallet or FakeWallet(recipient=_RECIPIENT)
    return {t.name: t for t in build_tools(conn, turn, wallet)}, turn


def _text(result):
    return result["content"][0]["text"]


def test_allowed_tools():
    assert len(TOOL_NAMES) == 20
    assert all(n.startswith("mcp__fa__") for n in ALLOWED_TOOLS)


def test_tool_names_match_built_tools(conn):
    tools, _ = _tools(conn)
    assert set(tools) == set(TOOL_NAMES)


def test_build_server(conn):
    server = build_server(conn, TurnState(notices=[], pending=[]), FakeWallet())
    assert server["name"] == "fa"


async def test_crear_contacto_adds_notice(conn):
    tools, turn = _tools(conn)
    await tools["crear_contacto"].handler({"nombre": "Fede"})
    assert turn.notices == ["(creé el contacto Fede)"]


async def test_renombrar_and_apodos_notices(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    await tools["renombrar_contacto"].handler({"contacto_id": c.id, "nombre": "Federico"})
    await tools["agregar_apodo"].handler({"contacto_id": c.id, "apodo": "mi hermano"})
    await tools["quitar_apodo"].handler({"contacto_id": c.id, "apodo": "mi hermano"})
    assert turn.notices == [
        "(renombré a Fede: ahora es Federico)",
        "(agregué a Federico el apodo mi hermano)",
        "(le saqué a Federico el apodo mi hermano)",
    ]


async def test_agregar_destino_first_is_primary(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "alias", "valor": "fede.mp"}
    )
    assert turn.notices == [
        "(agregué a Fede el alias fede.mp, quedó como principal, "
        "verificado en Fiwind: Federico Pérez, Brubank)"
    ]


async def test_agregar_destino_second_not_primary(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "alias", "valor": "fede.otro"}
    )
    assert turn.notices == [
        "(agregué a Fede el alias fede.otro, verificado en Fiwind: Federico Pérez, Brubank)"
    ]


async def test_invalid_destination_returns_error_without_notice(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    r = await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "cbu_cvu", "valor": "123"}
    )
    assert r["is_error"] is True and _text(r).startswith("Error:")
    assert turn.notices == []


async def test_not_found_returns_error(conn):
    tools, turn = _tools(conn)
    r = await tools["agregar_apodo"].handler({"contacto_id": 99, "apodo": "x"})
    assert r["is_error"] is True and _text(r).startswith("Error:")
    assert turn.notices == []


async def test_pedir_borrar_contacto_only_creates_pending(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    r = await tools["pedir_borrar_contacto"].handler({"contacto_id": c.id})
    assert [a.description for a in turn.pending] == ["Borrar a Fede"]
    assert "Acción pendiente creada: Borrar a Fede." in _text(r)
    assert "botones" in _text(r)
    assert get_contact(conn, c.id)
    assert turn.notices == []


async def test_pedir_cambio_valor_normalizes_and_validates(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    bad = await tools["pedir_cambio_valor_destino"].handler({"destino_id": d.id, "valor": "a"})
    assert bad["is_error"] is True and turn.pending == []
    await tools["pedir_cambio_valor_destino"].handler({"destino_id": d.id, "valor": "FEDE.NUEVO"})
    assert turn.pending[0].payload == {"destination_id": d.id, "value": "fede.nuevo"}


async def test_pedir_cambio_principal_and_borrar_destino(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    d2 = add_destination(conn, c.id, "alias", "fede.otro")
    await tools["pedir_cambio_principal"].handler({"destino_id": d2.id})
    await tools["pedir_borrar_destino"].handler({"destino_id": d2.id})
    assert [a.kind for a in turn.pending] == ["set_primary", "delete_destination"]


async def test_memory_tools(conn):
    tools, turn = _tools(conn)
    await tools["guardar_memoria"].handler({"texto": "cobrás el día 5"})
    note = list_notes(conn)[0]
    await tools["editar_memoria"].handler({"nota_id": note.id, "texto": "cobrás el 6"})
    listed = _text(await tools["listar_memoria"].handler({}))
    assert f"id={note.id}" in listed and "cobrás el 6" in listed
    await tools["borrar_memoria"].handler({"nota_id": note.id})
    assert turn.notices == [
        "(guardé en memoria: cobrás el día 5)",
        "(cambié en memoria: cobrás el 6)",
        "(borré de memoria: cobrás el 6)",
    ]
    assert list_notes(conn) == []


async def test_borrar_memoria_missing(conn):
    tools, turn = _tools(conn)
    r = await tools["borrar_memoria"].handler({"nota_id": 5})
    assert r["is_error"] is True and turn.notices == []


async def test_buscar_contactos_lists_ids_and_destinations(conn):
    tools, _ = _tools(conn)
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    text = _text(await tools["buscar_contactos"].handler({"texto": "fede"}))
    assert f"id={c.id}" in text and f"id={d.id}" in text and "fede.mp" in text
    assert "principal" in text


async def test_listar_contactos_empty_and_turn_reset(conn):
    tools, turn = _tools(conn)
    assert _text(await tools["listar_contactos"].handler({})) == "No hay contactos."
    await tools["crear_contacto"].handler({"nombre": "Fede"})
    turn.reset()
    assert turn.notices == [] and turn.pending == []


async def test_pedir_transferencia(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    result = await tools["pedir_transferencia"].handler({"contacto_id": c.id, "monto": "2000"})
    assert "Transferencia de $2.000 a Fede, alias fede.mp" in _text(result)
    assert "Federico" not in _text(result)
    assert "Federico Pérez" in turn.pending[0].description


async def test_pedir_transferencia_bad_amount(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    result = await tools["pedir_transferencia"].handler({"contacto_id": c.id, "monto": "dos mil"})
    assert result["is_error"] is True and "monto" in _text(result)
    assert turn.pending == []


async def test_pedir_transferencia_session_expired(conn):
    tools, turn = _tools(conn, FakeWallet(lookup_error=SessionExpired("buscar")))
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    result = await tools["pedir_transferencia"].handler({"contacto_id": c.id, "monto": "2000"})
    assert result["is_error"] is True and "venció" in _text(result)
    assert turn.session_expired is True
    assert turn.pending == []


async def test_pedir_transferencia_fiwind_unavailable(conn):
    tools, turn = _tools(conn, FakeWallet(lookup_error=FiwindUnavailable("buscar", "timeout")))
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    result = await tools["pedir_transferencia"].handler({"contacto_id": c.id, "monto": "2000"})
    assert result["is_error"] is True and "paso buscar" in _text(result)
    assert turn.session_expired is False


async def test_agregar_destino_verifies(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    result = await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "alias", "valor": "fede.mp"}
    )
    assert turn.notices == [
        "(agregué a Fede el alias fede.mp, quedó como principal, "
        "verificado en Fiwind: Federico Pérez, Brubank)"
    ]
    assert "Federico" not in _text(result) and "Verificado en Fiwind" in _text(result)
    assert "(verificado)" in _text(result)


async def test_agregar_destino_email_skips_fiwind(conn):
    wallet = FakeWallet()
    tools, turn = _tools(conn, wallet)
    c = create_contact(conn, "Fede")
    await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "email_mp", "valor": "fede@mail.com"}
    )
    assert wallet.calls == []
    assert len(turn.notices) == 1
    assert turn.notices[0].endswith("fede@mail.com, quedó como principal)")


async def test_agregar_destino_unverified_keeps_destination(conn):
    tools, turn = _tools(conn, FakeWallet(lookup_error=SessionExpired("buscar")))
    c = create_contact(conn, "Fede")
    result = await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "alias", "valor": "fede.mp"}
    )
    assert turn.notices[0].endswith("sin verificar: la sesión de Fiwind venció)")
    assert "Sin verificar: la sesión de Fiwind venció" in _text(result)
    assert turn.session_expired is True
    assert len(get_contact(conn, c.id).destinations) == 1


async def test_agregar_destino_not_found_in_fiwind(conn):
    tools, turn = _tools(conn, FakeWallet(recipient=None))
    c = create_contact(conn, "Fede")
    await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "alias", "valor": "fede.mp"}
    )
    assert turn.notices[0].endswith("sin verificar: Fiwind no encontró ese destino)")
    assert turn.session_expired is False


async def test_agregar_destino_without_browser(conn):
    tools, turn = _tools(conn, FakeWallet(available=False))
    c = create_contact(conn, "Fede")
    await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "alias", "valor": "fede.mp"}
    )
    assert turn.notices[0].endswith("sin verificar: agent-browser no está instalado en la PC)")


async def test_routine_tools(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Tío Pepe")
    add_destination(conn, c.id, "alias", "tio.pepe")
    r = await tools["crear_rutina"].handler({"contacto_id": c.id, "monto": "30000", "dia": 5})
    rid = int(_text(r).split("id=")[1].rstrip("."))
    await tools["editar_rutina"].handler({"rutina_id": rid, "monto": "35000"})
    listed = await tools["listar_rutinas"].handler({})
    await tools["borrar_rutina"].handler({"rutina_id": rid})
    assert turn.notices == [
        "(creé la rutina: $30.000 a Tío Pepe los días 5)",
        "(cambié la rutina: $35.000 a Tío Pepe los días 5)",
        "(borré la rutina: $35.000 a Tío Pepe los días 5)",
    ]
    assert f"id={rid} $35.000 a Tío Pepe los días 5" in _text(listed)


async def test_listar_rutinas_empty(conn):
    tools, _ = _tools(conn)
    assert _text(await tools["listar_rutinas"].handler({})) == "No hay rutinas."


async def test_editar_rutina_day_only(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Tío Pepe")
    r = await tools["crear_rutina"].handler({"contacto_id": c.id, "monto": "100", "dia": 5})
    rid = int(_text(r).split("id=")[1].rstrip("."))
    await tools["editar_rutina"].handler({"rutina_id": rid, "dia": 10})
    assert turn.notices[-1] == "(cambié la rutina: $100 a Tío Pepe los días 10)"


async def test_crear_rutina_bad_amount(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Tío Pepe")
    r = await tools["crear_rutina"].handler({"contacto_id": c.id, "monto": "mucho", "dia": 5})
    assert r["is_error"] is True and turn.notices == []


def test_turn_reset_clears_session_expired():
    turn = TurnState(session_expired=True)
    turn.reset()
    assert turn.session_expired is False
