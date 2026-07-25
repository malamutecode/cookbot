"""Temp-password generation + validation (STEP 44)."""

import pytest

from cookbot.models.password import (
    MIN_PASSWORD_LENGTH,
    MIN_TEMP_PASSWORD_LENGTH,
    TEMP_PASSWORD_ALPHABET,
    generate_temp_password,
    validate_password,
)

_DRAWS = 500


# ── generate_temp_password ────────────────────────────────────────────────────


def test_default_length_is_12():
    assert len(generate_temp_password()) == 12


def test_respects_requested_length():
    for n in (8, 10, 16, 32):
        assert len(generate_temp_password(n)) == n


def test_rejects_too_short_length():
    with pytest.raises(ValueError):
        generate_temp_password(MIN_TEMP_PASSWORD_LENGTH - 1)


def test_charset_contract_over_many_draws():
    """Every character comes from the unambiguous alphabet — no 0/O/1/l/I."""
    banned = set("0O1lI")
    for _ in range(_DRAWS):
        pw = generate_temp_password()
        assert set(pw) <= set(TEMP_PASSWORD_ALPHABET)
        assert not (set(pw) & banned)


def test_always_has_a_digit_upper_and_lower():
    for _ in range(_DRAWS):
        pw = generate_temp_password()
        assert any(c.isdigit() for c in pw), pw
        assert any(c.isupper() for c in pw), pw
        assert any(c.islower() for c in pw), pw


def test_passwords_are_not_repeated():
    """Collisions in 500 draws over a ~58^12 space would mean a broken RNG."""
    drawn = {generate_temp_password() for _ in range(_DRAWS)}
    assert len(drawn) == _DRAWS


def test_guaranteed_classes_are_not_pinned_to_the_first_positions():
    """The shuffle actually moves things — otherwise position 2 is always a digit."""
    third_chars = {generate_temp_password()[2] for _ in range(_DRAWS)}
    assert any(not c.isdigit() for c in third_chars)


def test_generated_password_always_passes_validation():
    for _ in range(_DRAWS):
        assert validate_password(generate_temp_password()) is None


# ── validate_password ─────────────────────────────────────────────────────────


def test_accepts_exactly_minimum_length():
    assert validate_password("a" * MIN_PASSWORD_LENGTH) is None


def test_rejects_one_below_minimum():
    err = validate_password("a" * (MIN_PASSWORD_LENGTH - 1))
    assert err is not None
    assert str(MIN_PASSWORD_LENGTH) in err


def test_rejects_empty_and_whitespace():
    assert validate_password("") is not None
    assert validate_password("   ") is not None


def test_rejects_surrounding_whitespace():
    assert validate_password(" hasloHaslo ") is not None


def test_accepts_a_long_password():
    assert validate_password("a-very-long-passphrase-indeed") is None
