"""Unit tests for the access-whitelist policy (email_allowed)."""

from app.auth_policy import email_allowed


def test_empty_list_allows_everyone():
    assert email_allowed("anyone@anywhere.com", []) is True
    assert email_allowed(None, []) is True
    assert email_allowed("", []) is True


def test_exact_email_match():
    allow = ["pawe213@gmail.com"]
    assert email_allowed("pawe213@gmail.com", allow) is True
    assert email_allowed("someone@gmail.com", allow) is False


def test_case_insensitive():
    assert email_allowed("PaWe213@Gmail.com", ["pawe213@gmail.com"]) is True
    assert email_allowed("a@x.com", ["A@X.COM"]) is True


def test_domain_match():
    allow = ["@example.com"]
    assert email_allowed("anyone@example.com", allow) is True
    assert email_allowed("boss@example.com", allow) is True
    assert email_allowed("intruder@other.com", allow) is False


def test_domain_does_not_match_subdomain_or_suffix():
    allow = ["@example.com"]
    assert email_allowed("x@sub.example.com", allow) is False
    assert email_allowed("x@notexample.com", allow) is False


def test_mixed_exact_and_domain():
    allow = ["boss@corp.com", "@team.com"]
    assert email_allowed("boss@corp.com", allow) is True
    assert email_allowed("dev@team.com", allow) is True
    assert email_allowed("dev@corp.com", allow) is False  # only boss@ from corp.com


def test_non_empty_list_denies_missing_email():
    allow = ["a@x.com"]
    assert email_allowed(None, allow) is False
    assert email_allowed("", allow) is False
    assert email_allowed("   ", allow) is False


def test_whitespace_and_blank_entries_ignored():
    # A list that is only blanks behaves like empty ⇒ allow all.
    assert email_allowed("a@x.com", ["", "  "]) is True
    # Surrounding whitespace on entries and input is tolerated.
    assert email_allowed(" a@x.com ", [" a@x.com "]) is True
