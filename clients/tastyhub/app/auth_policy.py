"""Access-whitelist policy — decide whether an authenticated email may use the app.

Pure, dependency-free so both the REST middleware and the WebSocket handler share
one rule and it stays unit-testable. See `Settings.allowed_emails`.
"""

from collections.abc import Iterable


def email_allowed(email: str | None, allowed: Iterable[str]) -> bool:
    """True if `email` may access the app.

    `allowed` entries are either an exact email ("a@x.com") or a whole domain
    beginning with "@" ("@example.com"). Matching is case-insensitive.

    EMPTY `allowed` ⇒ allow everyone (open sign-in). A non-empty list with a
    missing/blank email ⇒ denied (no email means we can't place them on the list).
    """
    allow_list = [a.strip().lower() for a in allowed if a and a.strip()]
    if not allow_list:
        return True
    if not email:
        return False
    e = email.strip().lower()
    if not e:
        return False
    domain = e[e.index("@"):] if "@" in e else ""
    for entry in allow_list:
        if entry.startswith("@"):
            if domain == entry:
                return True
        elif e == entry:
            return True
    return False
