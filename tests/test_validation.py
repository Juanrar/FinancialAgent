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
