import re
import unicodedata
from typing import Literal

DestinationType = Literal["email_mp", "cbu_cvu", "alias"]

DESTINATION_LABELS: dict[str, str] = {
    "email_mp": "email de Mercado Pago",
    "cbu_cvu": "CBU/CVU",
    "alias": "alias",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ALIAS_RE = re.compile(r"^[a-z0-9.-]{6,20}$")
_CBU_SEPARATORS_RE = re.compile(r"[\s\-.]")


class ValidationError(ValueError):
    """Error de validación con un mensaje en español listo para mostrarle a Juan."""


def cbu_check_digits_ok(digits: str) -> bool:
    if len(digits) != 22 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    w1 = [7, 1, 3, 9, 7, 1, 3]
    w2 = [3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3]
    check1 = (10 - sum(a * b for a, b in zip(d[0:7], w1)) % 10) % 10
    check2 = (10 - sum(a * b for a, b in zip(d[8:21], w2)) % 10) % 10
    return d[7] == check1 and d[21] == check2


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.split())


def detect_destination_type(value: str) -> DestinationType:
    if "@" in value:
        return "email_mp"
    if _CBU_SEPARATORS_RE.sub("", value).isdigit():
        return "cbu_cvu"
    return "alias"


def validate_destination(type_: str, value: str) -> str:
    if type_ == "email_mp":
        return _validate_email(value)
    if type_ == "cbu_cvu":
        return _validate_cbu_cvu(value)
    if type_ == "alias":
        return _validate_alias(value)
    raise ValidationError(f"Tipo de destino desconocido: {type_}.")


def _validate_email(value: str) -> str:
    email = value.strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValidationError("El email no tiene un formato válido.")
    return email


def _validate_cbu_cvu(value: str) -> str:
    digits = _CBU_SEPARATORS_RE.sub("", value)
    if len(digits) != 22:
        raise ValidationError(
            f"El CBU/CVU tiene que tener 22 dígitos y tiene {len(digits)}."
        )
    if not digits.isdigit():
        raise ValidationError("El CBU/CVU solo puede tener dígitos.")
    if not cbu_check_digits_ok(digits):
        raise ValidationError("El CBU/CVU no es válido, revisá que no tenga errores de tipeo.")
    return digits


def _validate_alias(value: str) -> str:
    alias = value.strip().lower()
    if not _ALIAS_RE.match(alias):
        raise ValidationError(
            "El alias tiene que tener entre 6 y 20 caracteres: "
            "letras sin tildes, números, puntos o guiones."
        )
    return alias
