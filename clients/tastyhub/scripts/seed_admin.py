"""Seed the first Feedek admin user in Firebase Auth.

Creates (or finds) a Firebase Auth account for an email, prints its **uid** and,
for a freshly created account, a **random temporary password** you log in with
and change later. Put the printed uid into `ADMIN_UIDS` when you deploy — the
backend marks that uid `role="admin"` on first sight (STEP 42).

Idempotent: if the account already exists, it is NOT modified — the script just
prints the existing uid (and does not reveal or reset the password).

Auth: uses Application Default Credentials, same as the app
(`firebase_admin.initialize_app()`), so authenticate first with:
    gcloud auth application-default login
and make sure the project is set (GOOGLE_CLOUD_PROJECT or gcloud config).

Run (from clients/tastyhub):
    uv run python scripts/seed_admin.py                       # defaults to pawe213@gmail.com
    uv run python scripts/seed_admin.py --email you@example.com
    uv run python scripts/seed_admin.py --password 'MyChosenPass123'   # set a specific one

Prereq: Firebase Auth Email/Password provider must be ENABLED
(Firebase console → Authentication → Sign-in method → Email/Password).
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys

import firebase_admin
import firebase_admin.auth as fb_auth

DEFAULT_EMAIL = "pawe213@gmail.com"


def _random_password(length: int = 20) -> str:
    """A strong temporary password: letters + digits + a few symbols, guaranteed
    to satisfy Firebase's 6-char minimum and typical complexity expectations."""
    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    # Ensure at least one of each class so it passes any downstream policy.
    base = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%_-"),
    ]
    base += [secrets.choice(alphabet) for _ in range(length - len(base))]
    secrets.SystemRandom().shuffle(base)
    return "".join(base)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the first Feedek admin in Firebase Auth.")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help=f"admin email (default: {DEFAULT_EMAIL})")
    parser.add_argument(
        "--password",
        default=None,
        help="set a specific password instead of a random one (min 6 chars)",
    )
    args = parser.parse_args()

    # ADC, same as the app. No service-account key file needed.
    firebase_admin.initialize_app()

    # Already exists? Don't touch it — just report the uid.
    try:
        existing = fb_auth.get_user_by_email(args.email)
        print("User already exists — not modified.")
        print(f"  email : {existing.email}")
        print(f"  uid   : {existing.uid}")
        print()
        print("Add this uid to ADMIN_UIDS on deploy:")
        print(f"  _ADMIN_UIDS=\"{existing.uid}\"")
        print()
        print("Forgot the password? Send a reset from the Firebase console")
        print("(Authentication → user → ⋮ → Reset password) or your app's reset flow.")
        return 0
    except fb_auth.UserNotFoundError:
        pass

    password = args.password or _random_password()
    if len(password) < 6:
        print("ERROR: password must be at least 6 characters.", file=sys.stderr)
        return 1

    user = fb_auth.create_user(
        email=args.email,
        password=password,
        email_verified=False,
    )

    print("Created Firebase Auth user.")
    print(f"  email             : {user.email}")
    print(f"  uid               : {user.uid}")
    print(f"  temporary password: {password}")
    print()
    print("SAVE the temporary password now — it is not stored anywhere else.")
    print("Log in with it, then change it (app reset flow or Firebase console).")
    print()
    print("Add this uid to ADMIN_UIDS on deploy:")
    print(f"  _ADMIN_UIDS=\"{user.uid}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
