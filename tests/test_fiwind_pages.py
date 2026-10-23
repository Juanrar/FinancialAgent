import pytest

from financial_agent.fiwind.pages import (
    ConfirmDialog,
    PageFormatError,
    Recipient,
    parse_confirm_dialog,
    parse_dashboard_balance,
    parse_form_balance,
    parse_lookup,
    parse_result_dialog,
    same_holder,
)
from fiwind_samples import (
    ALIAS,
    BANK,
    CONFIRM_DIALOG,
    CVU,
    DASHBOARD,
    HOLDER,
    LOOKUP_FOUND,
    LOOKUP_NOT_FOUND,
    RESULT_DIALOG,
)


def test_parse_lookup_found():
    assert parse_lookup(LOOKUP_FOUND) == Recipient(HOLDER, BANK, CVU)


def test_parse_lookup_cbu_label():
    assert parse_lookup(LOOKUP_FOUND.replace("- CVU:", "- CBU:")).account == CVU


def test_parse_lookup_not_found():
    assert parse_lookup(LOOKUP_NOT_FOUND) is None


def test_parse_lookup_unknown_page():
    with pytest.raises(PageFormatError):
        parse_lookup("Algo que no es el formulario")


def test_parse_form_balance():
    assert parse_form_balance(LOOKUP_FOUND) == 1500
    assert parse_form_balance(LOOKUP_FOUND.replace("(15,00 ARS)", "(1.234,56 ARS)")) == 123456
    assert parse_form_balance(LOOKUP_NOT_FOUND) is None


def test_parse_confirm_dialog():
    assert parse_confirm_dialog(CONFIRM_DIALOG) == ConfirmDialog(1000, HOLDER, CVU, ALIAS, BANK)
    with pytest.raises(PageFormatError):
        parse_confirm_dialog("close\nConfirmar envío\nEnviar")


def test_parse_confirm_dialog_without_alias():
    text = CONFIRM_DIALOG.replace(f"ALIAS\n{ALIAS}\n", "")
    assert parse_confirm_dialog(text).alias is None


def test_parse_confirm_dialog_bad_amount():
    with pytest.raises(PageFormatError):
        parse_confirm_dialog(CONFIRM_DIALOG.replace("10,00 ARS", "diez"))


def test_parse_result_dialog():
    assert parse_result_dialog(RESULT_DIALOG) == "En proceso"
    assert parse_result_dialog("close\nAlgo salió mal\nEntendido") is None


def test_parse_dashboard_balance():
    assert parse_dashboard_balance(DASHBOARD) == 300
    big = DASHBOARD.replace('"3"', '"1.234"').replace('"00"', '"56"')
    assert parse_dashboard_balance(big) == 123456
    hidden = DASHBOARD.replace('StaticText "3"', 'StaticText "****"').replace(
        'StaticText "00"', 'StaticText "**"')
    assert parse_dashboard_balance(hidden) is None


def test_same_holder():
    assert same_holder("Ana María  Pérez", "ana maria perez")
    assert not same_holder("Ana María Pérez", "Ana Pérez")
