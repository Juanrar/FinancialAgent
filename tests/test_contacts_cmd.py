from types import SimpleNamespace
from unittest.mock import AsyncMock

from financial_agent.bot.awaiting import Awaiting, get_awaiting, set_awaiting
from financial_agent.bot.contacts_cmd import (
    contact_keyboard,
    contacts_list_keyboard,
    format_contact,
    handle_awaiting,
    on_contacts,
    on_contacts_callback,
)
from financial_agent.store import contacts as store
from financial_agent.store.contacts import Contact, Destination

CVU = "0000003100000000000017"


def _contact():
    d1 = Destination(id=7, contact_id=1, type="cbu_cvu", value=CVU, is_primary=True)
    d2 = Destination(id=8, contact_id=1, type="alias", value="fede.mp", is_primary=False)
    return Contact(id=1, name="Fede", nicknames=("mi hermano",), destinations=(d1, d2))


def test_format_contact():
    assert format_contact(_contact()) == (
        "Fede\nApodos: mi hermano\nDestinos:\n"
        "1. CBU/CVU 0000003100000000000017 (principal)\n2. alias fede.mp"
    )
    assert format_contact(Contact(id=2, name="Ana", nicknames=(), destinations=())) == (
        "Ana\nApodos: ninguno\nDestinos: ninguno"
    )


def test_contact_keyboard_hides_primary_button_for_primary():
    rows = contact_keyboard(_contact()).inline_keyboard
    dest_rows = [[b.callback_data for b in r] for r in rows if r[0].callback_data.startswith("d:")]
    assert dest_rows == [["d:edit:7", "d:del:7"], ["d:edit:8", "d:primary:8", "d:del:8"]]
    assert rows[-1][0].callback_data == "c:list"


def test_contact_keyboard_top_rows():
    rows = contact_keyboard(_contact()).inline_keyboard
    assert [(b.text, b.callback_data) for b in rows[0]] == [
        ("Editar nombre", "c:rename:1"),
        ("Reemplazar apodos", "c:nicks:1"),
    ]
    assert [(b.text, b.callback_data) for b in rows[1]] == [
        ("Agregar destino", "c:adddest:1"),
        ("Borrar contacto", "c:del:1"),
    ]


def test_contacts_list_keyboard():
    rows = contacts_list_keyboard([_contact()]).inline_keyboard
    assert [(b.text, b.callback_data) for b in rows[0]] == [("Fede", "c:show:1")]
    assert contacts_list_keyboard([]) is None


def _context(conn, settings):
    bot_data = {"conn": conn, "settings": settings}
    app = SimpleNamespace(bot_data=bot_data)
    return SimpleNamespace(application=app, bot_data=bot_data, user_data={})


def _message_update(text=None):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_message=message)


def _callback_update(data):
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=message
    )
    return SimpleNamespace(callback_query=query, effective_message=message)


def _seed(conn):
    contact = store.create_contact(conn, "Fede")
    store.add_nickname(conn, contact.id, "mi hermano")
    d1 = store.add_destination(conn, contact.id, "cbu_cvu", CVU)
    d2 = store.add_destination(conn, contact.id, "alias", "fede.mp")
    return contact, d1, d2


async def test_on_contacts_empty(conn, settings):
    update = _message_update()
    await on_contacts(update, _context(conn, settings))
    call = update.effective_message.reply_text.await_args
    assert call.args[0] == "No hay contactos. Los creás hablando conmigo."
    assert call.kwargs["reply_markup"] is None


async def test_on_contacts_lists_buttons(conn, settings):
    contact, _, _ = _seed(conn)
    update = _message_update()
    await on_contacts(update, _context(conn, settings))
    call = update.effective_message.reply_text.await_args
    assert call.args[0] == "Contactos:"
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"c:show:{contact.id}"


async def test_show_renders_detail(conn, settings):
    contact, _, _ = _seed(conn)
    update = _callback_update(f"c:show:{contact.id}")
    context = _context(conn, settings)
    set_awaiting(context, Awaiting("contact_rename", contact.id))
    await on_contacts_callback(update, context)
    call = update.callback_query.edit_message_text.await_args
    assert call.args[0].startswith("Fede\nApodos: mi hermano")
    assert get_awaiting(context) is None
    update.callback_query.answer.assert_awaited()


async def test_show_missing_contact(conn, settings):
    update = _callback_update("c:show:99")
    await on_contacts_callback(update, _context(conn, settings))
    update.callback_query.message.reply_text.assert_awaited_with("Ese contacto ya no existe.")


async def test_list_callback(conn, settings):
    _seed(conn)
    update = _callback_update("c:list")
    await on_contacts_callback(update, _context(conn, settings))
    assert update.callback_query.edit_message_text.await_args.args[0] == "Contactos:"


async def test_list_callback_empty(conn, settings):
    update = _callback_update("c:list")
    await on_contacts_callback(update, _context(conn, settings))
    update.callback_query.edit_message_text.assert_awaited_with(
        "No hay contactos. Los creás hablando conmigo.", reply_markup=None
    )


async def test_prompts_set_awaiting(conn, settings):
    contact, d1, _ = _seed(conn)
    cases = [
        (f"c:rename:{contact.id}", Awaiting("contact_rename", contact.id), "Mandame el nombre nuevo."),
        (
            f"c:nicks:{contact.id}",
            Awaiting("contact_nicknames", contact.id),
            "Mandame los apodos separados por coma.",
        ),
        (
            f"c:adddest:{contact.id}",
            Awaiting("destination_add", contact.id),
            "Mandame el email de Mercado Pago, CBU/CVU o alias.",
        ),
        (f"d:edit:{d1.id}", Awaiting("destination_value", d1.id), "Mandame el valor nuevo."),
    ]
    for data, awaiting, text in cases:
        context = _context(conn, settings)
        update = _callback_update(data)
        await on_contacts_callback(update, context)
        assert get_awaiting(context) == awaiting
        call = update.callback_query.message.reply_text.await_args
        assert call.args[0] == text
        button = call.kwargs["reply_markup"].inline_keyboard[0][0]
        assert (button.text, button.callback_data) == ("Cancelar", f"c:show:{contact.id}")


async def test_edit_missing_destination(conn, settings):
    context = _context(conn, settings)
    update = _callback_update("d:edit:99")
    await on_contacts_callback(update, context)
    update.callback_query.message.reply_text.assert_awaited_with("Ese destino ya no existe.")
    assert get_awaiting(context) is None


async def test_delete_contact_asks_then_deletes(conn, settings):
    contact, _, _ = _seed(conn)
    context = _context(conn, settings)
    update = _callback_update(f"c:del:{contact.id}")
    await on_contacts_callback(update, context)
    call = update.callback_query.edit_message_text.await_args
    assert call.args[0] == "¿Seguro que querés borrar a Fede?"
    row = call.kwargs["reply_markup"].inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in row] == [
        ("Sí, borrar", f"c:delok:{contact.id}"),
        ("No", f"c:show:{contact.id}"),
    ]
    assert len(store.list_contacts(conn)) == 1

    update = _callback_update(f"c:delok:{contact.id}")
    await on_contacts_callback(update, context)
    assert store.list_contacts(conn) == []
    assert update.callback_query.edit_message_text.await_args.args[0] == (
        "No hay contactos. Los creás hablando conmigo."
    )


async def test_delete_contact_missing(conn, settings):
    update = _callback_update("c:delok:99")
    await on_contacts_callback(update, _context(conn, settings))
    update.callback_query.message.reply_text.assert_awaited_with("Ese contacto ya no existe.")


async def test_delete_destination_asks_then_deletes(conn, settings):
    contact, d1, d2 = _seed(conn)
    context = _context(conn, settings)
    update = _callback_update(f"d:del:{d2.id}")
    await on_contacts_callback(update, context)
    call = update.callback_query.edit_message_text.await_args
    assert call.args[0] == "¿Seguro que querés borrar el alias fede.mp?"
    row = call.kwargs["reply_markup"].inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in row] == [
        ("Sí, borrar", f"d:delok:{d2.id}"),
        ("No", f"c:show:{contact.id}"),
    ]

    update = _callback_update(f"d:delok:{d2.id}")
    await on_contacts_callback(update, context)
    assert [d.id for d in store.get_contact(conn, contact.id).destinations] == [d1.id]
    assert update.callback_query.edit_message_text.await_args.args[0].startswith("Fede\n")


async def test_delete_destination_missing(conn, settings):
    update = _callback_update("d:del:99")
    await on_contacts_callback(update, _context(conn, settings))
    update.callback_query.message.reply_text.assert_awaited_with("Ese destino ya no existe.")


async def test_primary_marks_and_redraws(conn, settings):
    contact, d1, d2 = _seed(conn)
    update = _callback_update(f"d:primary:{d2.id}")
    await on_contacts_callback(update, _context(conn, settings))
    dests = store.get_contact(conn, contact.id).destinations
    assert [d.is_primary for d in dests] == [False, True]
    assert "2. alias fede.mp (principal)" in update.callback_query.edit_message_text.await_args.args[0]


async def test_rename_awaiting(conn, settings):
    contact, _, _ = _seed(conn)
    context = _context(conn, settings)
    awaiting = Awaiting("contact_rename", contact.id)
    set_awaiting(context, awaiting)
    update = _message_update("Federico")
    await handle_awaiting(update, context, awaiting)
    assert store.get_contact(conn, contact.id).name == "Federico"
    assert get_awaiting(context) is None
    assert update.effective_message.reply_text.await_args.args[0].startswith("Federico\n")


async def test_rename_empty_keeps_awaiting(conn, settings):
    contact, _, _ = _seed(conn)
    context = _context(conn, settings)
    awaiting = Awaiting("contact_rename", contact.id)
    set_awaiting(context, awaiting)
    update = _message_update("   ")
    await handle_awaiting(update, context, awaiting)
    update.effective_message.reply_text.assert_awaited_with("El nombre del contacto no puede estar vacío.")
    assert get_awaiting(context) == awaiting


async def test_nicknames_awaiting(conn, settings):
    contact, _, _ = _seed(conn)
    context = _context(conn, settings)
    awaiting = Awaiting("contact_nicknames", contact.id)
    set_awaiting(context, awaiting)
    update = _message_update("fefe, el fede")
    await handle_awaiting(update, context, awaiting)
    assert store.get_contact(conn, contact.id).nicknames == ("el fede", "fefe")
    assert get_awaiting(context) is None
    assert "Apodos: el fede, fefe" in update.effective_message.reply_text.await_args.args[0]


async def test_destination_add_cvu_with_spaces(conn, settings):
    contact = store.create_contact(conn, "Ana")
    context = _context(conn, settings)
    awaiting = Awaiting("destination_add", contact.id)
    set_awaiting(context, awaiting)
    update = _message_update("0000003 1000000 00000017")
    await handle_awaiting(update, context, awaiting)
    dests = store.get_contact(conn, contact.id).destinations
    assert [(d.type, d.value) for d in dests] == [("cbu_cvu", CVU)]
    assert get_awaiting(context) is None
    assert "1. CBU/CVU " + CVU + " (principal)" in update.effective_message.reply_text.await_args.args[0]


async def test_destination_add_invalid_keeps_awaiting(conn, settings):
    contact = store.create_contact(conn, "Ana")
    context = _context(conn, settings)
    awaiting = Awaiting("destination_add", contact.id)
    set_awaiting(context, awaiting)
    update = _message_update("ab")
    await handle_awaiting(update, context, awaiting)
    assert get_awaiting(context) == awaiting
    assert store.get_contact(conn, contact.id).destinations == ()
    update.effective_message.reply_text.assert_awaited_once()


async def test_destination_add_contact_gone(conn, settings):
    context = _context(conn, settings)
    awaiting = Awaiting("destination_add", 99)
    set_awaiting(context, awaiting)
    update = _message_update("fede.mp.ok")
    await handle_awaiting(update, context, awaiting)
    update.effective_message.reply_text.assert_awaited_with("Ese contacto ya no existe.")
    assert get_awaiting(context) is None


async def test_destination_value_awaiting(conn, settings):
    contact, d1, _ = _seed(conn)
    context = _context(conn, settings)
    awaiting = Awaiting("destination_value", d1.id)
    set_awaiting(context, awaiting)
    update = _message_update("0000003100000000000024")
    await handle_awaiting(update, context, awaiting)
    assert store.get_destination(conn, d1.id).value == "0000003100000000000024"
    assert get_awaiting(context) is None
    assert update.effective_message.reply_text.await_args.args[0].startswith("Fede\n")


async def test_destination_value_invalid_keeps_awaiting(conn, settings):
    _, d1, _ = _seed(conn)
    context = _context(conn, settings)
    awaiting = Awaiting("destination_value", d1.id)
    set_awaiting(context, awaiting)
    update = _message_update("123")
    await handle_awaiting(update, context, awaiting)
    assert get_awaiting(context) == awaiting
    assert store.get_destination(conn, d1.id).value == CVU


async def test_destination_value_gone(conn, settings):
    context = _context(conn, settings)
    awaiting = Awaiting("destination_value", 99)
    set_awaiting(context, awaiting)
    update = _message_update("x")
    await handle_awaiting(update, context, awaiting)
    update.effective_message.reply_text.assert_awaited_with("Ese destino ya no existe.")
    assert get_awaiting(context) is None
