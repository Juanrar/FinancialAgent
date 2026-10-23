from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from financial_agent.bot.awaiting import Awaiting, get_awaiting, set_awaiting
from financial_agent.bot.memory_cmd import (
    PAGE_NOTES,
    format_notes,
    handle_awaiting,
    notes_keyboard,
    on_memory,
    on_memory_callback,
    paginate_notes,
)
from financial_agent.store import memory
from financial_agent.store.memory import Note

TZ = "America/Argentina/Buenos_Aires"
T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)


def test_format_notes():
    notes = [Note(id=5, text="cobro el día 5", updated_at=datetime(2026, 10, 20, 15, tzinfo=UTC))]
    page = paginate_notes(notes)[0]
    assert format_notes(page, TZ, 0, 1) == "Memoria:\n1. cobro el día 5 (20/10/2026)"
    assert format_notes([], TZ, 0, 1) == "No hay notas guardadas."


def test_format_notes_uses_local_date():
    notes = [Note(id=1, text="a", updated_at=datetime(2026, 10, 21, 1, tzinfo=UTC))]
    assert format_notes(paginate_notes(notes)[0], TZ, 0, 1) == "Memoria:\n1. a (20/10/2026)"


def test_notes_keyboard():
    notes = [
        Note(id=5, text="a", updated_at=datetime.now(UTC)),
        Note(id=9, text="b", updated_at=datetime.now(UTC)),
    ]
    rows = notes_keyboard(paginate_notes(notes)[0], 0, 1).inline_keyboard
    assert [(b.text, b.callback_data) for b in rows[1]] == [
        ("Editar 2", "m:edit:9"),
        ("Borrar 2", "m:del:9"),
    ]
    assert notes_keyboard([], 0, 1) is None


def _notes(n, size=200):
    return [Note(i, "x" * size, T0) for i in range(1, n + 1)]


def test_pages_fit_telegram_limit():
    pages = paginate_notes(_notes(60))
    assert [pos for page in pages for pos, _ in page] == list(range(1, 61))
    for i, page in enumerate(pages):
        assert len(page) <= PAGE_NOTES
        assert len(format_notes(page, TZ, i, len(pages))) <= 4096


def test_pages_split_by_chars():
    pages = paginate_notes(_notes(30, 1000))
    assert [pos for page in pages for pos, _ in page] == list(range(1, 31))
    assert all(len(page) <= 3 for page in pages)
    for i, page in enumerate(pages):
        assert len(format_notes(page, TZ, i, len(pages))) <= 4096


def test_long_note_is_cut_in_listing():
    text = format_notes(paginate_notes(_notes(1, 5000))[0], TZ, 0, 1)
    assert len(text) <= 4096 and "…" in text


def test_header_shows_page_only_when_many():
    pages = paginate_notes(_notes(25))
    assert format_notes(pages[1], TZ, 1, len(pages)).startswith("Memoria (página 2 de 3):")
    assert format_notes(paginate_notes(_notes(2))[0], TZ, 0, 1).startswith("Memoria:")


def test_keyboard_navigation():
    pages = paginate_notes(_notes(25))
    first = notes_keyboard(pages[0], 0, 3).inline_keyboard[-1]
    middle = notes_keyboard(pages[1], 1, 3).inline_keyboard[-1]
    last = notes_keyboard(pages[2], 2, 3).inline_keyboard[-1]
    assert [(b.text, b.callback_data) for b in first] == [("Siguiente", "m:page:1")]
    assert [b.callback_data for b in middle] == ["m:page:0", "m:page:2"]
    assert [(b.text, b.callback_data) for b in last] == [("Anterior", "m:page:1")]
    single = notes_keyboard(paginate_notes(_notes(2))[0], 0, 1).inline_keyboard
    assert all(b.callback_data.startswith(("m:edit", "m:del")) for row in single for b in row)


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


async def test_on_memory_lists_notes(conn, settings):
    note = memory.add_note(conn, "cobro el 5")
    update = _message_update()
    await on_memory(update, _context(conn, settings))
    call = update.effective_message.reply_text.await_args
    assert call.args[0].startswith("Memoria:\n1. cobro el 5")
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"m:edit:{note.id}"


async def test_on_memory_empty(conn, settings):
    update = _message_update()
    await on_memory(update, _context(conn, settings))
    call = update.effective_message.reply_text.await_args
    assert call.args[0] == "No hay notas guardadas."
    assert call.kwargs["reply_markup"] is None


async def test_delete_removes_note_and_refreshes_list(conn, settings):
    a = memory.add_note(conn, "a")
    memory.add_note(conn, "b")
    update = _callback_update(f"m:del:{a.id}")
    await on_memory_callback(update, _context(conn, settings))
    assert [n.text for n in memory.list_notes(conn)] == ["b"]
    call = update.callback_query.edit_message_text.await_args
    assert call.args[0].startswith("Memoria:\n1. b")
    update.callback_query.answer.assert_awaited()


async def test_delete_missing_note(conn, settings):
    update = _callback_update("m:del:99")
    await on_memory_callback(update, _context(conn, settings))
    update.callback_query.message.reply_text.assert_awaited_with("Esa nota ya no existe.")


async def test_edit_sets_awaiting_and_asks_for_text(conn, settings):
    memory.add_note(conn, "a")
    note = memory.add_note(conn, "b")
    context = _context(conn, settings)
    update = _callback_update(f"m:edit:{note.id}")
    await on_memory_callback(update, context)
    assert get_awaiting(context) == Awaiting("note_edit", note.id)
    call = update.callback_query.message.reply_text.await_args
    assert call.args[0] == "Mandame el texto nuevo para la nota 2."
    button = call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert (button.text, button.callback_data) == ("Cancelar", "m:cancel")


async def test_edit_missing_note(conn, settings):
    context = _context(conn, settings)
    update = _callback_update("m:edit:99")
    await on_memory_callback(update, context)
    update.callback_query.message.reply_text.assert_awaited_with("Esa nota ya no existe.")
    assert get_awaiting(context) is None


async def test_cancel_clears_awaiting(conn, settings):
    context = _context(conn, settings)
    set_awaiting(context, Awaiting("note_edit", 1))
    update = _callback_update("m:cancel")
    await on_memory_callback(update, context)
    assert get_awaiting(context) is None
    update.callback_query.edit_message_text.assert_awaited_with("Edición cancelada.")


async def test_handle_awaiting_replaces_note(conn, settings):
    note = memory.add_note(conn, "viejo")
    context = _context(conn, settings)
    awaiting = Awaiting("note_edit", note.id)
    set_awaiting(context, awaiting)
    update = _message_update("nuevo")
    await handle_awaiting(update, context, awaiting)
    assert memory.list_notes(conn)[0].text == "nuevo"
    assert get_awaiting(context) is None
    first, second = update.effective_message.reply_text.await_args_list
    assert first.args[0] == "Nota actualizada."
    assert second.args[0].startswith("Memoria:\n1. nuevo")


async def test_handle_awaiting_empty_text_keeps_awaiting(conn, settings):
    note = memory.add_note(conn, "viejo")
    context = _context(conn, settings)
    awaiting = Awaiting("note_edit", note.id)
    set_awaiting(context, awaiting)
    update = _message_update("   ")
    await handle_awaiting(update, context, awaiting)
    update.effective_message.reply_text.assert_awaited_with("La nota no puede estar vacía.")
    assert get_awaiting(context) == awaiting
    assert memory.list_notes(conn)[0].text == "viejo"


async def test_handle_awaiting_note_gone(conn, settings):
    context = _context(conn, settings)
    awaiting = Awaiting("note_edit", 99)
    set_awaiting(context, awaiting)
    update = _message_update("nuevo")
    await handle_awaiting(update, context, awaiting)
    update.effective_message.reply_text.assert_awaited_with("Esa nota ya no existe.")
    assert get_awaiting(context) is None


def _add(conn, n):
    return [memory.add_note(conn, f"nota {i}") for i in range(1, n + 1)]


async def test_page_callback_edits_message(conn, settings):
    _add(conn, 25)
    update = _callback_update("m:page:1")
    await on_memory_callback(update, _context(conn, settings))
    call = update.callback_query.edit_message_text.await_args
    assert "página 2 de 3" in call.args[0]
    assert call.args[0].splitlines()[1].startswith("11. nota 11")


async def test_page_callback_clamps_index(conn, settings):
    _add(conn, 25)
    update = _callback_update("m:page:9")
    await on_memory_callback(update, _context(conn, settings))
    assert "página 3 de 3" in update.callback_query.edit_message_text.await_args.args[0]


async def test_delete_redraws_same_page(conn, settings):
    notes = _add(conn, 25)
    update = _callback_update(f"m:del:{notes[12].id}")
    await on_memory_callback(update, _context(conn, settings))
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "página 2 de 3" in text


async def test_delete_last_note_of_last_page_goes_back(conn, settings):
    notes = _add(conn, 21)
    update = _callback_update(f"m:del:{notes[20].id}")
    await on_memory_callback(update, _context(conn, settings))
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "página 2 de 2" in text


async def test_edit_shows_global_position(conn, settings):
    notes = _add(conn, 25)
    update = _callback_update(f"m:edit:{notes[14].id}")
    await on_memory_callback(update, _context(conn, settings))
    assert update.callback_query.message.reply_text.await_args.args[0] == (
        "Mandame el texto nuevo para la nota 15."
    )


async def test_handle_awaiting_shows_page_with_note(conn, settings):
    notes = _add(conn, 25)
    context = _context(conn, settings)
    awaiting = Awaiting("note_edit", notes[22].id)
    set_awaiting(context, awaiting)
    update = _message_update("nuevo")
    await handle_awaiting(update, context, awaiting)
    second = update.effective_message.reply_text.await_args_list[1]
    assert "página 3 de 3" in second.args[0]
