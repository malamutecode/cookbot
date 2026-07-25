"""Temporary-password generation + validation (STEP 44).

Pure and dependency-free so it is unit-testable without Firebase or FastAPI:
the admin-create flow generates a temp password here, and the self-service
"set your own password" route validates the replacement here.

Nothing tenant-specific lives in this module (Architecture Rule 5) — the Polish
error strings are product copy shared by every tenant, same as `ui_strings`.
"""

import secrets

# Unambiguous alphabet — no 0/O, 1/l/I. A temp password is read off a screen and
# typed by hand, so visually confusable characters are a support cost.
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_LOWER = "abcdefghijkmnopqrstuvwxyz"
_DIGITS = "23456789"
TEMP_PASSWORD_ALPHABET = _UPPER + _LOWER + _DIGITS

#: Firebase itself refuses anything under 6 characters; we require more.
MIN_PASSWORD_LENGTH = 8

#: Shortest temp password we will generate — below this the "at least one of
#: each class" guarantee stops being meaningful.
MIN_TEMP_PASSWORD_LENGTH = 8


def generate_temp_password(length: int = 12) -> str:
    """A cryptographically random temporary password.

    Guarantees at least one uppercase, one lowercase and one digit so the result
    always satisfies a password policy that expects mixed classes, and draws the
    remainder uniformly from `TEMP_PASSWORD_ALPHABET`.

    Raises:
        ValueError: if `length` is below `MIN_TEMP_PASSWORD_LENGTH`.
    """
    if length < MIN_TEMP_PASSWORD_LENGTH:
        raise ValueError(f"length must be >= {MIN_TEMP_PASSWORD_LENGTH}")
    chars = [
        secrets.choice(_UPPER),
        secrets.choice(_LOWER),
        secrets.choice(_DIGITS),
    ]
    chars += [secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(length - len(chars))]
    # Shuffle so the guaranteed classes aren't always in positions 0/1/2.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def validate_password(password: str) -> str | None:
    """Return a Polish error message, or `None` when the password is acceptable.

    Deliberately only a length minimum — complexity classes and breach-list
    checks are deferred (see TASK.md STEP 44); Firebase enforces its own floor
    server-side regardless.
    """
    if not password or not password.strip():
        return "Hasło nie może być puste."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Hasło musi mieć co najmniej {MIN_PASSWORD_LENGTH} znaków."
    if password != password.strip():
        return "Hasło nie może zaczynać się ani kończyć spacją."
    return None
