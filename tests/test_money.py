import pytest

from financial_agent.money import (
    amount_input,
    format_amount,
    format_fiwind_amount,
    parse_amount,
    parse_fiwind_amount,
)
from financial_agent.validation import ValidationError


@pytest.mark.parametrize("text,cents", [
    ("2000", 200000), ("2000.50", 200050), ("2000.5", 200050), ("2000,50", 200050),
    ("2.000", 200000), ("2.000,50", 200050), ("$2.000", 200000), (" $ 2000 ", 200000),
    ("1", 100), ("1.234.567,89", 123456789),
])
def test_parse_amount(text, cents):
    assert parse_amount(text) == cents


@pytest.mark.parametrize("text", ["0", "0.99", "-5", "abc", "2000.123", "", "2,000.50"])
def test_parse_amount_rejects(text):
    with pytest.raises(ValidationError):
        parse_amount(text)


def test_format_amount():
    assert format_amount(200000) == "$2.000"
    assert format_amount(200050) == "$2.000,50"
    assert format_amount(100) == "$1"
    assert format_amount(123456789) == "$1.234.567,89"


def test_fiwind_amounts():
    assert format_fiwind_amount(150000) == "1.500,00 ARS"
    assert parse_fiwind_amount("1.500,00 ARS") == 150000
    assert parse_fiwind_amount("3,00") == 300
    assert amount_input(200000) == "2000"
    assert amount_input(200050) == "2000.50"
