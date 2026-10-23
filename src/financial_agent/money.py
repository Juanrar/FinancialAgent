"""Montos en pesos como enteros en centavos: parseo y formato."""

import re
from decimal import Decimal

from financial_agent.validation import ValidationError

_PLAIN = re.compile(r"^\d+([.,]\d{1,2})?$")
_THOUSANDS = re.compile(r"^\d{1,3}(\.\d{3})+(,\d{1,2})?$")

_INVALID = "El monto tiene que ser un número en pesos, por ejemplo 2000 o 2000.50."
_MINIMUM = "El monto mínimo es $1."


def _to_cents(text: str) -> int | None:
    cleaned = text.replace("$", "").replace(" ", "")
    if _PLAIN.match(cleaned):
        number = cleaned.replace(",", ".")
    elif _THOUSANDS.match(cleaned):
        number = cleaned.replace(".", "").replace(",", ".")
    else:
        return None
    return int(Decimal(number) * 100)


def parse_amount(text: str) -> int:
    cents = _to_cents(text)
    if cents is None:
        raise ValidationError(_INVALID)
    if cents < 100:
        raise ValidationError(_MINIMUM)
    return cents


def _grouped(cents: int) -> tuple[str, str]:
    pesos, rest = divmod(cents, 100)
    return f"{pesos:,}".replace(",", "."), f"{rest:02d}"


def format_amount(cents: int) -> str:
    pesos, rest = _grouped(cents)
    return f"${pesos}" if rest == "00" else f"${pesos},{rest}"


def format_fiwind_amount(cents: int) -> str:
    pesos, rest = _grouped(cents)
    return f"{pesos},{rest} ARS"


def parse_fiwind_amount(text: str) -> int:
    cleaned = text.strip().removesuffix("ARS").strip()
    cents = _to_cents(cleaned)
    if cents is None:
        raise ValidationError(_INVALID)
    return cents


def amount_input(cents: int) -> str:
    pesos, rest = divmod(cents, 100)
    return str(pesos) if rest == 0 else f"{pesos}.{rest:02d}"
