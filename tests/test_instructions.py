from datetime import UTC, datetime

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
    for phrase in ["voseo", "Ve al grano", "pregunt", "botones", "transferir"]:
        assert phrase in p


def test_prompt_omits_title_and_bold_rules():
    p = build_system_prompt([])
    assert "Negrita" not in p and "Títulos en mayúscula" not in p
