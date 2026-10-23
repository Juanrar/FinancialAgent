import re
from dataclasses import dataclass

from financial_agent.money import parse_fiwind_amount
from financial_agent.validation import ValidationError, normalize_text

_ACCOUNT_LINE_RE = re.compile(r"^- (?:CVU|CBU): (\d{22})$")
_FORM_BALANCE_RE = re.compile(r"\(([\d.]+,\d{2}) ARS\)")
_NOT_FOUND = "No se encontraron resultados"
_BALANCE_LABEL = 'StaticText "Balance disponible"'
_STATIC_TEXT_RE = re.compile(r'^(?:- )?StaticText "(.*)"$')


class PageFormatError(Exception):
    pass


@dataclass(frozen=True)
class Recipient:
    holder: str
    bank: str
    account: str


@dataclass(frozen=True)
class ConfirmDialog:
    amount_cents: int
    holder: str
    account: str
    alias: str | None
    bank: str


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _amount(text: str) -> int:
    try:
        return parse_fiwind_amount(text)
    except ValidationError:
        raise PageFormatError("monto ilegible") from None


def _value_after(lines: list[str], *labels: str) -> str | None:
    for i, line in enumerate(lines[:-1]):
        if line in labels:
            return lines[i + 1]
    return None


def parse_lookup(form_text: str) -> Recipient | None:
    if _NOT_FOUND in form_text:
        return None
    lines = _lines(form_text)
    for i, line in enumerate(lines):
        match = _ACCOUNT_LINE_RE.match(line)
        if match and i >= 2:
            return Recipient(holder=lines[i - 2], bank=lines[i - 1], account=match.group(1))
    raise PageFormatError("formulario de destinatario desconocido")


def parse_form_balance(form_text: str) -> int | None:
    match = _FORM_BALANCE_RE.search(form_text)
    return _amount(match.group(1)) if match else None


def parse_confirm_dialog(text: str) -> ConfirmDialog:
    lines = _lines(text)
    amount = _value_after(lines, "Vas a enviar")
    holder = _value_after(lines, "Nombre y apellido")
    account = _value_after(lines, "CVU", "CBU")
    bank = _value_after(lines, "Banco")
    if amount is None or holder is None or account is None or bank is None:
        raise PageFormatError("diálogo de confirmación incompleto")
    return ConfirmDialog(
        amount_cents=_amount(amount),
        holder=holder,
        account=account,
        alias=_value_after(lines, "ALIAS"),
        bank=bank,
    )


def parse_result_dialog(text: str) -> str | None:
    return _value_after(_lines(text), "Estado")


def parse_dashboard_balance(snapshot: str) -> int | None:
    lines = _lines(snapshot)
    start = next(
        (i for i, line in enumerate(lines) if line.removeprefix("- ") == _BALANCE_LABEL), None
    )
    if start is None:
        return None
    whole = cents = None
    for line in lines[start + 1 : start + 11]:
        match = _STATIC_TEXT_RE.match(line)
        if not match:
            continue
        value = match.group(1)
        if whole is None:
            if re.fullmatch(r"[\d.]+", value):
                whole = value
        elif re.fullmatch(r"\d{2}", value):
            cents = value
            break
    if whole is None or cents is None:
        return None
    return _amount(f"{whole},{cents}")


def same_holder(a: str, b: str) -> bool:
    return normalize_text(a) == normalize_text(b)
