"""Orquestación de transferencias: elige y verifica el destino, crea la acción pendiente
y, ya reclamada por el bot, corre el retiro y traduce el resultado.

Los textos con titular, banco o CVU son solo para Juan. A Claude le llega `agent_note`,
que sale de una lista fija de resultados y del `agent_text` de la acción.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from financial_agent.fiwind.client import (
    Expired, FiwindClient, Insufficient, Mismatch, NotSent, Sent, Unconfirmed, WithdrawalOrder,
)
from financial_agent.money import format_amount
from financial_agent.store import contacts, pending
from financial_agent.store.contacts import Contact, Destination
from financial_agent.store.pending import PendingAction
from financial_agent.validation import DESTINATION_LABELS, ValidationError, account_label

TRANSFERABLE_TYPES = ("cbu_cvu", "alias")
SENT_STATUS = "En proceso"

_NO_BROWSER = "No puedo verificar el destino porque agent-browser no está instalado en la PC."
_NOT_FOUND = "Fiwind no encontró ese destino. Revisá el valor."


class DestinationError(ValidationError):
    """No se pudo elegir destino. `for_juan` es el motivo escrito para Juan; el mensaje
    normal está escrito para Claude."""

    def __init__(self, message: str, for_juan: str):
        super().__init__(message)
        self.for_juan = for_juan


@dataclass(frozen=True)
class TransferReport:
    text: str
    agent_note: str
    manual_action_id: int | None = None
    follow_up: PendingAction | None = None
    session_expired: bool = False


def _no_emails(contact: Contact) -> DestinationError:
    return DestinationError(
        "Fiwind no transfiere a emails de Mercado Pago. "
        f"Pedile a Juan un CBU, CVU o alias de {contact.name}.",
        "Fiwind no transfiere a emails de Mercado Pago",
    )


def choose_destination(contact: Contact, destination_id: int | None) -> Destination:
    if destination_id is not None:
        for dest in contact.destinations:
            if dest.id == destination_id:
                if dest.type not in TRANSFERABLE_TYPES:
                    raise _no_emails(contact)
                return dest
        raise ValidationError(f"Ese destino no es de {contact.name}.")
    if not contact.destinations:
        raise DestinationError(
            f"{contact.name} no tiene destinos. Pedile a Juan un CBU, CVU o alias.",
            f"{contact.name} no tiene CBU, CVU ni alias",
        )
    options = [d for d in contact.destinations if d.type in TRANSFERABLE_TYPES]
    if not options:
        raise _no_emails(contact)
    for dest in options:
        if dest.is_primary:
            return dest
    if len(options) == 1:
        return options[0]
    listing = "; ".join(f"id={d.id} {DESTINATION_LABELS[d.type]} {d.value}" for d in options)
    raise DestinationError(
        f"{contact.name} tiene varios destinos y ninguno es principal. "
        f"Preguntale a Juan cuál usar: {listing}",
        f"{contact.name} tiene varios destinos y ninguno es principal",
    )


async def verify_destination(
    conn: sqlite3.Connection, wallet: FiwindClient, destination: Destination
) -> Destination:
    if destination.verified:
        return destination
    if not wallet.available:
        raise ValidationError(_NO_BROWSER)
    recipient = await wallet.lookup(destination.value)
    if recipient is None:
        raise ValidationError(_NOT_FOUND)
    return contacts.set_verification(
        conn, destination.id, recipient.holder, recipient.account, recipient.bank
    )


async def prepare_transfer(
    conn: sqlite3.Connection,
    wallet: FiwindClient,
    contact_id: int,
    amount_cents: int,
    destination_id: int | None = None,
    routine_id: int | None = None,
    now: datetime | None = None,
) -> PendingAction:
    contact = contacts.get_contact(conn, contact_id)
    destination = await verify_destination(
        conn, wallet, choose_destination(contact, destination_id)
    )
    text = (
        f"Transferencia de {format_amount(amount_cents)} a {contact.name}, "
        f"{DESTINATION_LABELS[destination.type]} {destination.value}"
    )
    payload = {"destination_id": destination.id, "amount_cents": amount_cents, "agent_text": text}
    ttl = pending.ROUTINE_TTL if routine_id is not None else pending.PENDING_TTL
    return pending.create_action(
        conn, "transfer", payload, now=now, ttl=ttl, routine_id=routine_id
    )


def manual_instructions(conn: sqlite3.Connection, action: PendingAction) -> str:
    dest = contacts.get_destination(conn, action.payload["destination_id"])
    lines = [
        "Podés hacerla a mano desde Fiwind con estos datos:",
        f"Titular: {dest.holder}",
        f"{account_label(dest.account)}: {dest.account}",
    ]
    if dest.type == "alias":
        lines.append(f"Alias: {dest.value}")
    lines.append(f"Monto: {format_amount(action.payload['amount_cents'])}")
    return "\n".join(lines)


def _note(action: PendingAction, result: str) -> str:
    return f"Resultado de la transferencia ({pending.agent_text(action)}): {result}"


async def run_transfer(
    conn: sqlite3.Connection, wallet: FiwindClient, action: PendingAction
) -> TransferReport:
    dest = contacts.get_destination(conn, action.payload["destination_id"])
    order = WithdrawalOrder(dest.value, dest.holder, dest.account, action.payload["amount_cents"])
    outcome = await wallet.withdraw(order)

    if isinstance(outcome, Sent) and outcome.status != SENT_STATUS:
        pending.finish_transfer(conn, action.id, "sin_confirmar", "otro estado")
        return TransferReport(
            f"No sé si salió: Fiwind mostró el estado {outcome.status}. "
            "Revisá los movimientos de Fiwind antes de pedirla de nuevo.",
            _note(action, "sin confirmar, Fiwind mostró otro estado"),
        )
    if isinstance(outcome, Sent):
        pending.finish_transfer(conn, action.id, "enviada")
        return TransferReport(
            f"Listo. Fiwind aceptó la transferencia. Estado: {outcome.status}.",
            _note(action, "enviada"),
        )
    if isinstance(outcome, NotSent):
        pending.finish_transfer(conn, action.id, "no_salio", outcome.step)
        return TransferReport(
            f"No salió: falló el paso {outcome.step} ({outcome.reason}). No se mandó nada.",
            _note(action, f"no salió en el paso {outcome.step}"),
            manual_action_id=action.id,
        )
    if isinstance(outcome, Insufficient):
        pending.finish_transfer(conn, action.id, "no_salio", "saldo insuficiente")
        return TransferReport(
            f"No salió: el saldo de Fiwind es {format_amount(outcome.balance_cents)}. "
            "Cargá Fiwind y pedímela de nuevo.",
            _note(action, "no salió por falta de saldo"),
        )
    if isinstance(outcome, Mismatch):
        pending.finish_transfer(conn, action.id, "no_salio", "los datos del destino cambiaron")
        found = outcome.found
        follow_up = pending.create_action(
            conn,
            "update_verification",
            {
                "destination_id": dest.id,
                "holder": found.holder,
                "account": found.account,
                "bank": found.bank,
            },
        )
        return TransferReport(
            "No salió: Fiwind muestra otros datos para ese destino. Si son correctos, "
            "confirmá la actualización y pedí la transferencia de nuevo.",
            _note(action, "no salió porque cambiaron los datos del destino"),
            follow_up=follow_up,
        )
    if isinstance(outcome, Expired):
        pending.finish_transfer(conn, action.id, "no_salio", "venció la sesión")
        return TransferReport(
            "No salió: la sesión de Fiwind venció.",
            _note(action, "no salió porque venció la sesión de Fiwind"),
            manual_action_id=action.id,
            session_expired=True,
        )
    pending.finish_transfer(conn, action.id, "sin_confirmar", outcome.step)
    return TransferReport(
        f"No sé si salió: falló el paso {outcome.step} después de tocar Enviar. "
        "Revisá los movimientos de Fiwind antes de pedirla de nuevo.",
        _note(action, "sin confirmar, falló después de tocar Enviar"),
    )
