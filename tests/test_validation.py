import pytest

from financial_agent.validation import (
    ValidationError,
    cbu_check_digits_ok,
    detect_destination_type,
    normalize_text,
    validate_destination,
)

VALID_CBU = "2850590940090418135201"
VALID_CVU = "0000003100000000000017"

def test_cbu_cvu_valid_check_digits():
    assert cbu_check_digits_ok(VALID_CBU) and cbu_check_digits_ok(VALID_CVU)
    assert validate_destination("cbu_cvu", VALID_CVU) == VALID_CVU

def test_cbu_cvu_bad_check_digit():
    with pytest.raises(ValidationError):
        validate_destination("cbu_cvu", "2850590940090418135202")

def test_cbu_cvu_wrong_length():
    with pytest.raises(ValidationError, match="22"):
        validate_destination("cbu_cvu", "123")

def test_cbu_cvu_accepts_spaces_and_hyphens():
    assert validate_destination("cbu_cvu", "00000031 0000000000-0017") == VALID_CVU

def test_alias_rules():
    assert validate_destination("alias", " Juan.Perez-MP ") == "juan.perez-mp"
    for bad in ["corto", "a" * 21, "con espacio", "ñandú.mp", "x_y_z_w"]:
        with pytest.raises(ValidationError):
            validate_destination("alias", bad)

def test_email_mp():
    assert validate_destination("email_mp", " Fede@Mail.com ") == "fede@mail.com"
    with pytest.raises(ValidationError):
        validate_destination("email_mp", "fede@")

def test_unknown_type():
    with pytest.raises(ValidationError):
        validate_destination("cvu", VALID_CVU)

def test_detect_destination_type():
    assert detect_destination_type("a@b.com") == "email_mp"
    assert detect_destination_type("00000031 0000000000 0017") == "cbu_cvu"
    assert detect_destination_type("123") == "cbu_cvu"
    assert detect_destination_type("fede.mp") == "alias"

def test_normalize_text():
    assert normalize_text("  Martín   PÉREZ ") == "martin perez"


def test_cbu_rejects_unicode_digits():
    value = "٠" * 22  # ceros arábigo-índicos: str.isdigit() los acepta
    assert not cbu_check_digits_ok(value)
    with pytest.raises(ValidationError):
        validate_destination("cbu_cvu", value)


@pytest.mark.parametrize("value,expected", [
    ("12345678", "alias"),
    ("12345678901234567890", "alias"),
    ("0000168300000000123459", "cbu_cvu"),
    ("00001683 0000000012345-9", "cbu_cvu"),
    ("000016830000000012345", "cbu_cvu"),  # 21 dígitos: CBU mal tipeado
    ("fede@mail.com", "email_mp"),
    ("fede.mp", "alias"),
])
def test_detect_destination_type(value, expected):
    assert detect_destination_type(value) == expected
