from datetime import UTC, date, datetime

from financial_agent.agent.instructions import build_system_prompt
from financial_agent.store.memory import Note


def test_prompt_lists_notes_with_ids():
    notes = [Note(id=3, text="cobro el día 5", updated_at=datetime.now(UTC))]
    p = build_system_prompt(notes)
    assert "[id=3] cobro el día 5" in p


def test_prompt_without_notes():
    assert "No hay notas guardadas." in build_system_prompt([])


def test_prompt_has_style_and_contact_rules():
    p = build_system_prompt([])
    for phrase in ["voseo", "Ve al grano", "pregunt", "botones", "pedir_transferencia"]:
        assert phrase in p


def test_prompt_omits_title_and_bold_rules():
    p = build_system_prompt([])
    assert "Negrita" not in p and "Títulos en mayúscula" not in p


def test_prompt_explains_forwarded_messages():
    assert "[Mensaje reenviado]" in build_system_prompt([])


def test_prompt_lists_routines_and_transfers():
    prompt = build_system_prompt([], ["[id=1] $30.000 a Tío Pepe los días 5"])
    assert "[id=1] $30.000 a Tío Pepe los días 5" in prompt and "pedir_transferencia" in prompt
    assert "Rutinas:\nNo hay rutinas." in build_system_prompt([])
    assert "todavía no podés" not in prompt


def test_prompt_has_today_and_history_tool():
    prompt = build_system_prompt([], today=date(2026, 10, 28))
    assert "Hoy es 28/10/2026." in prompt and "listar_transferencias" in prompt
    assert "Hoy es" not in build_system_prompt([])
