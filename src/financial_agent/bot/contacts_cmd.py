from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from financial_agent.bot.awaiting import Awaiting, clear_awaiting, set_awaiting
from financial_agent.bot.callback_data import make_callback, parse_callback
from financial_agent.store import contacts as store
from financial_agent.store.contacts import Contact
from financial_agent.store.db import NotFoundError
from financial_agent.validation import DESTINATION_LABELS, ValidationError, detect_destination_type

CONTACT = "c"
DESTINATION = "d"
MSG_EMPTY = "No hay contactos. Los creás hablando conmigo."
MSG_LIST = "Contactos:"
MSG_CONTACT_GONE = "Ese contacto ya no existe."
MSG_DESTINATION_GONE = "Ese destino ya no existe."
PROMPTS = {
    "contact_rename": "Mandame el nombre nuevo.",
    "contact_nicknames": "Mandame los apodos separados por coma.",
    "destination_add": "Mandame el email de Mercado Pago, CBU/CVU o alias.",
    "destination_value": "Mandame el valor nuevo.",
}


def _button(text: str, prefix: str, action: str, item_id: int | None = None):
    return InlineKeyboardButton(text, callback_data=make_callback(prefix, action, item_id))


def format_contact(contact: Contact) -> str:
    nicknames = ", ".join(contact.nicknames) if contact.nicknames else "ninguno"
    lines = [contact.name, f"Apodos: {nicknames}"]
    if not contact.destinations:
        lines.append("Destinos: ninguno")
    else:
        lines.append("Destinos:")
        for n, dest in enumerate(contact.destinations, start=1):
            suffix = " (principal)" if dest.is_primary else ""
            lines.append(f"{n}. {DESTINATION_LABELS[dest.type]} {dest.value}{suffix}")
    return "\n".join(lines)


def contacts_list_keyboard(contacts: list[Contact]) -> InlineKeyboardMarkup | None:
    if not contacts:
        return None
    return InlineKeyboardMarkup(
        [[_button(contact.name, CONTACT, "show", contact.id)] for contact in contacts]
    )


def contact_keyboard(contact: Contact) -> InlineKeyboardMarkup:
    rows = [
        [
            _button("Editar nombre", CONTACT, "rename", contact.id),
            _button("Reemplazar apodos", CONTACT, "nicks", contact.id),
        ],
        [
            _button("Agregar destino", CONTACT, "adddest", contact.id),
            _button("Borrar contacto", CONTACT, "del", contact.id),
        ],
    ]
    for n, dest in enumerate(contact.destinations, start=1):
        row = [_button(f"Editar {n}", DESTINATION, "edit", dest.id)]
        if not dest.is_primary:
            row.append(_button(f"Principal {n}", DESTINATION, "primary", dest.id))
        row.append(_button(f"Borrar {n}", DESTINATION, "del", dest.id))
        rows.append(row)
    rows.append([_button("Volver", CONTACT, "list")])
    return InlineKeyboardMarkup(rows)


def _conn(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["conn"]


def _render_list(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup | None]:
    contacts = store.list_contacts(_conn(context))
    if not contacts:
        return MSG_EMPTY, None
    return MSG_LIST, contacts_list_keyboard(contacts)


def _render_contact(
    context: ContextTypes.DEFAULT_TYPE, contact_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    contact = store.get_contact(_conn(context), contact_id)
    return format_contact(contact), contact_keyboard(contact)


def _confirm_keyboard(yes: str, id_: int, prefix: str, back_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _button("Sí, borrar", prefix, yes, id_),
                _button("No", CONTACT, "show", back_id),
            ]
        ]
    )


async def on_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = _render_list(context)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def _ask(
    query, context: ContextTypes.DEFAULT_TYPE, kind: str, target_id: int, contact_id: int
) -> None:
    set_awaiting(context, Awaiting(kind, target_id))
    cancel = InlineKeyboardMarkup([[_button("Cancelar", CONTACT, "show", contact_id)]])
    await query.message.reply_text(PROMPTS[kind], reply_markup=cancel)


async def _contact_action(
    query, context: ContextTypes.DEFAULT_TYPE, action: str, contact_id: int | None
) -> None:
    conn = _conn(context)
    if action == "list":
        clear_awaiting(context)
        text, keyboard = _render_list(context)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "show":
        clear_awaiting(context)
        text, keyboard = _render_contact(context, contact_id)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "rename":
        store.get_contact(conn, contact_id)
        await _ask(query, context, "contact_rename", contact_id, contact_id)
    elif action == "nicks":
        store.get_contact(conn, contact_id)
        await _ask(query, context, "contact_nicknames", contact_id, contact_id)
    elif action == "adddest":
        store.get_contact(conn, contact_id)
        await _ask(query, context, "destination_add", contact_id, contact_id)
    elif action == "del":
        contact = store.get_contact(conn, contact_id)
        await query.edit_message_text(
            f"¿Seguro que querés borrar a {contact.name}?",
            reply_markup=_confirm_keyboard("delok", contact_id, CONTACT, contact_id),
        )
    elif action == "delok":
        store.delete_contact(conn, contact_id)
        text, keyboard = _render_list(context)
        await query.edit_message_text(text, reply_markup=keyboard)


async def _destination_action(
    query, context: ContextTypes.DEFAULT_TYPE, action: str, destination_id: int
) -> None:
    conn = _conn(context)
    dest = store.get_destination(conn, destination_id)
    if action == "edit":
        await _ask(query, context, "destination_value", destination_id, dest.contact_id)
    elif action == "primary":
        store.set_primary(conn, destination_id)
        text, keyboard = _render_contact(context, dest.contact_id)
        await query.edit_message_text(text, reply_markup=keyboard)
    elif action == "del":
        label = DESTINATION_LABELS[dest.type]
        await query.edit_message_text(
            f"¿Seguro que querés borrar el {label} {dest.value}?",
            reply_markup=_confirm_keyboard("delok", destination_id, DESTINATION, dest.contact_id),
        )
    elif action == "delok":
        store.delete_destination(conn, destination_id)
        text, keyboard = _render_contact(context, dest.contact_id)
        await query.edit_message_text(text, reply_markup=keyboard)


async def on_contacts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    prefix, action, item_id = parse_callback(query.data)
    try:
        if prefix == CONTACT:
            await _contact_action(query, context, action, item_id)
        else:
            await _destination_action(query, context, action, item_id)
    except NotFoundError:
        clear_awaiting(context)
        gone = MSG_CONTACT_GONE if prefix == CONTACT else MSG_DESTINATION_GONE
        await query.message.reply_text(gone)


async def _apply_awaiting(
    context: ContextTypes.DEFAULT_TYPE, awaiting: Awaiting, text: str
) -> int:
    """Aplica el texto de Juan y devuelve el id del contacto a redibujar."""
    conn = _conn(context)
    if awaiting.kind == "contact_rename":
        return store.rename_contact(conn, awaiting.target_id, text).id
    if awaiting.kind == "contact_nicknames":
        nicknames = [part for part in text.split(",") if part.strip()]
        return store.replace_nicknames(conn, awaiting.target_id, nicknames).id
    if awaiting.kind == "destination_add":
        store.add_destination(conn, awaiting.target_id, detect_destination_type(text), text)
        return awaiting.target_id
    return store.update_destination_value(conn, awaiting.target_id, text).contact_id


async def handle_awaiting(
    update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: Awaiting
) -> None:
    message = update.effective_message
    try:
        contact_id = await _apply_awaiting(context, awaiting, message.text)
        rendered = _render_contact(context, contact_id)
    except ValidationError as error:
        await message.reply_text(str(error))
        return
    except NotFoundError:
        clear_awaiting(context)
        gone = MSG_DESTINATION_GONE if awaiting.kind == "destination_value" else MSG_CONTACT_GONE
        await message.reply_text(gone)
        return
    clear_awaiting(context)
    text, keyboard = rendered
    await message.reply_text(text, reply_markup=keyboard)
