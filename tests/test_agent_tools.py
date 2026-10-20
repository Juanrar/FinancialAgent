from financial_agent.agent.tools import (
    ALLOWED_TOOLS,
    TOOL_NAMES,
    TurnState,
    build_server,
    build_tools,
)
from financial_agent.store.contacts import add_destination, create_contact, get_contact
from financial_agent.store.memory import list_notes


def _tools(conn):
    turn = TurnState(notices=[], pending=[])
    return {t.name: t for t in build_tools(conn, turn)}, turn


def _text(result):
    return result["content"][0]["text"]


def test_allowed_tools():
    assert len(TOOL_NAMES) == 15
    assert all(n.startswith("mcp__fa__") for n in ALLOWED_TOOLS)


def test_tool_names_match_built_tools(conn):
    tools, _ = _tools(conn)
    assert set(tools) == set(TOOL_NAMES)


def test_build_server(conn):
    server = build_server(conn, TurnState(notices=[], pending=[]))
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
    assert turn.notices == ["(agregué a Fede el alias fede.mp, quedó como principal)"]


async def test_agregar_destino_second_not_primary(conn):
    tools, turn = _tools(conn)
    c = create_contact(conn, "Fede")
    add_destination(conn, c.id, "alias", "fede.mp")
    await tools["agregar_destino"].handler(
        {"contacto_id": c.id, "tipo": "alias", "valor": "fede.otro"}
    )
    assert turn.notices == ["(agregué a Fede el alias fede.otro)"]


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
