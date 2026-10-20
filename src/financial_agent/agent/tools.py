import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from financial_agent.store import contacts, memory, pending
from financial_agent.store.contacts import Contact
from financial_agent.store.db import NotFoundError
from financial_agent.store.pending import PendingAction
from financial_agent.validation import DESTINATION_LABELS, ValidationError, validate_destination

SERVER_NAME = "fa"

TOOL_NAMES: tuple[str, ...] = (
    "buscar_contactos",
    "listar_contactos",
    "crear_contacto",
    "renombrar_contacto",
    "agregar_apodo",
    "quitar_apodo",
    "agregar_destino",
    "pedir_cambio_principal",
    "pedir_cambio_valor_destino",
    "pedir_borrar_destino",
    "pedir_borrar_contacto",
    "listar_memoria",
    "guardar_memoria",
    "editar_memoria",
    "borrar_memoria",
)

ALLOWED_TOOLS: list[str] = [f"mcp__{SERVER_NAME}__{n}" for n in TOOL_NAMES]


@dataclass
class TurnState:
    notices: list[str] = field(default_factory=list)
    pending: list[PendingAction] = field(default_factory=list)

    def reset(self) -> None:
        self.notices.clear()
        self.pending.clear()


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "is_error": True}


def _safe(fn: Callable[[dict], Awaitable[dict]]) -> Callable[[dict], Awaitable[dict]]:
    async def wrapper(args: dict) -> dict:
        try:
            return await fn(args)
        except (ValidationError, NotFoundError) as error:
            return _error(str(error))

    return wrapper


def _format_contact(contact: Contact) -> str:
    nicks = ", ".join(contact.nicknames) if contact.nicknames else "ninguno"
    if contact.destinations:
        dests = "; ".join(
            f"id={d.id} {DESTINATION_LABELS[d.type]} {d.value}"
            + (" (principal)" if d.is_primary else "")
            for d in contact.destinations
        )
    else:
        dests = "ninguno"
    return f"id={contact.id} {contact.name} | apodos: {nicks} | destinos: {dests}"


def _format_contacts(found: list[Contact]) -> str:
    if not found:
        return "No hay contactos."
    return "\n".join(_format_contact(c) for c in found)


def build_tools(conn: sqlite3.Connection, turn: TurnState) -> list[SdkMcpTool]:
    def request(kind: str, payload: dict) -> dict:
        action = pending.create_action(conn, kind, payload)
        turn.pending.append(action)
        return _ok(
            f"Acción pendiente creada: {action.description}. "
            "Juan tiene que confirmarla con los botones."
        )

    @tool(
        "buscar_contactos",
        "Busca contactos por nombre o apodo (sin distinguir mayúsculas ni tildes). "
        "Usala antes de actuar sobre un contacto para obtener su id.",
        {"texto": str},
    )
    @_safe
    async def buscar_contactos(args):
        return _ok(_format_contacts(contacts.search_contacts(conn, args["texto"])))

    @tool("listar_contactos", "Lista todos los contactos con sus apodos y destinos.", {})
    @_safe
    async def listar_contactos(args):
        return _ok(_format_contacts(contacts.list_contacts(conn)))

    @tool(
        "crear_contacto",
        "Crea un contacto nuevo. Usala cuando Juan menciona a una persona que todavía no existe.",
        {"nombre": str},
    )
    @_safe
    async def crear_contacto(args):
        contact = contacts.create_contact(conn, args["nombre"])
        turn.notices.append(f"(creé el contacto {contact.name})")
        return _ok(_format_contact(contact))

    @tool(
        "renombrar_contacto",
        "Cambia el nombre de un contacto.",
        {"contacto_id": int, "nombre": str},
    )
    @_safe
    async def renombrar_contacto(args):
        old = contacts.get_contact(conn, args["contacto_id"])
        new = contacts.rename_contact(conn, args["contacto_id"], args["nombre"])
        turn.notices.append(f"(renombré a {old.name}: ahora es {new.name})")
        return _ok(_format_contact(new))

    @tool(
        "agregar_apodo",
        "Agrega un apodo a un contacto. Usala cuando Juan describe a un contacto, por ejemplo "
        '"Fede es mi hermano" agrega el apodo "mi hermano".',
        {"contacto_id": int, "apodo": str},
    )
    @_safe
    async def agregar_apodo(args):
        contact = contacts.add_nickname(conn, args["contacto_id"], args["apodo"])
        turn.notices.append(f"(agregué a {contact.name} el apodo {args['apodo']})")
        return _ok(_format_contact(contact))

    @tool("quitar_apodo", "Quita un apodo de un contacto.", {"contacto_id": int, "apodo": str})
    @_safe
    async def quitar_apodo(args):
        contact = contacts.remove_nickname(conn, args["contacto_id"], args["apodo"])
        turn.notices.append(f"(le saqué a {contact.name} el apodo {args['apodo']})")
        return _ok(_format_contact(contact))

    @tool(
        "agregar_destino",
        "Agrega un destino de pago a un contacto. tipo es email_mp, cbu_cvu o alias. "
        "El primer destino de un contacto queda como principal.",
        {"contacto_id": int, "tipo": str, "valor": str},
    )
    @_safe
    async def agregar_destino(args):
        dest = contacts.add_destination(conn, args["contacto_id"], args["tipo"], args["valor"])
        contact = contacts.get_contact(conn, args["contacto_id"])
        label = DESTINATION_LABELS[dest.type]
        suffix = ", quedó como principal" if dest.is_primary else ""
        turn.notices.append(f"(agregué a {contact.name} el {label} {dest.value}{suffix})")
        return _ok(_format_contact(contact))

    @tool(
        "pedir_cambio_principal",
        "Pide marcar un destino como el principal de su contacto. Juan lo confirma con botones.",
        {"destino_id": int},
    )
    @_safe
    async def pedir_cambio_principal(args):
        return request("set_primary", {"destination_id": args["destino_id"]})

    @tool(
        "pedir_cambio_valor_destino",
        "Pide cambiar el valor de un destino (por ejemplo corregir un alias). "
        "Juan lo confirma con botones.",
        {"destino_id": int, "valor": str},
    )
    @_safe
    async def pedir_cambio_valor_destino(args):
        dest = contacts.get_destination(conn, args["destino_id"])
        value = validate_destination(dest.type, args["valor"])
        return request("update_destination_value", {"destination_id": dest.id, "value": value})

    @tool(
        "pedir_borrar_destino",
        "Pide borrar un destino de pago. Juan lo confirma con botones.",
        {"destino_id": int},
    )
    @_safe
    async def pedir_borrar_destino(args):
        return request("delete_destination", {"destination_id": args["destino_id"]})

    @tool(
        "pedir_borrar_contacto",
        "Pide borrar un contacto con todos sus destinos. Juan lo confirma con botones.",
        {"contacto_id": int},
    )
    @_safe
    async def pedir_borrar_contacto(args):
        return request("delete_contact", {"contact_id": args["contacto_id"]})

    @tool("listar_memoria", "Lista las notas de memoria con su id.", {})
    @_safe
    async def listar_memoria(args):
        notes = memory.list_notes(conn)
        if not notes:
            return _ok("No hay notas guardadas.")
        return _ok("\n".join(f"id={n.id} {n.text}" for n in notes))

    @tool(
        "guardar_memoria",
        "Guarda una nota de memoria sobre Juan: datos, preferencias, contexto. "
        "No uses la memoria para datos de contactos.",
        {"texto": str},
    )
    @_safe
    async def guardar_memoria(args):
        note = memory.add_note(conn, args["texto"])
        turn.notices.append(f"(guardé en memoria: {note.text})")
        return _ok(f"Nota guardada con id={note.id}.")

    @tool(
        "editar_memoria",
        "Cambia el texto de una nota de memoria.",
        {"nota_id": int, "texto": str},
    )
    @_safe
    async def editar_memoria(args):
        note = memory.update_note(conn, args["nota_id"], args["texto"])
        turn.notices.append(f"(cambié en memoria: {note.text})")
        return _ok(f"Nota {note.id} actualizada.")

    @tool("borrar_memoria", "Borra una nota de memoria.", {"nota_id": int})
    @_safe
    async def borrar_memoria(args):
        note = memory.delete_note(conn, args["nota_id"])
        turn.notices.append(f"(borré de memoria: {note.text})")
        return _ok(f"Nota {note.id} borrada.")

    return [
        buscar_contactos,
        listar_contactos,
        crear_contacto,
        renombrar_contacto,
        agregar_apodo,
        quitar_apodo,
        agregar_destino,
        pedir_cambio_principal,
        pedir_cambio_valor_destino,
        pedir_borrar_destino,
        pedir_borrar_contacto,
        listar_memoria,
        guardar_memoria,
        editar_memoria,
        borrar_memoria,
    ]


def build_server(conn: sqlite3.Connection, turn: TurnState) -> McpSdkServerConfig:
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=build_tools(conn, turn))
