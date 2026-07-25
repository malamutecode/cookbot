"""UserRecord field defaults + round-trip (STEP 44).

The point of these tests is backward compatibility: STEP 42 documents already in
Firestore have no `display_name` / `must_change_password` keys, and they must
still deserialize.
"""

from cookbot.models.user import TokenQuota, UserRecord


def _legacy_dict() -> dict:
    """Exactly what a pre-STEP-44 `users/{uid}.record` map looks like."""
    return {
        "uid": "legacy-uid",
        "email": "legacy@example.com",
        "role": "user",
        "quota": {"daily_limit": 1000, "monthly_limit": 25000},
        "disabled": False,
    }


def test_legacy_document_deserializes_with_new_fields_defaulted():
    rec = UserRecord.model_validate(_legacy_dict())
    assert rec.uid == "legacy-uid"
    assert rec.display_name is None
    assert rec.must_change_password is False
    # Existing fields untouched.
    assert rec.quota.daily_limit == 1000
    assert rec.role == "user"


def test_new_fields_round_trip():
    rec = UserRecord(
        uid="u1",
        email="u1@example.com",
        display_name="Jan Kowalski",
        role="admin",
        quota=TokenQuota(daily_limit=5, monthly_limit=50),
        must_change_password=True,
    )
    again = UserRecord.model_validate(rec.model_dump(mode="json"))
    assert again == rec
    assert again.display_name == "Jan Kowalski"
    assert again.must_change_password is True


def test_minimal_record_defaults():
    rec = UserRecord(uid="u2")
    assert rec.email is None
    assert rec.display_name is None
    assert rec.role == "user"
    assert rec.disabled is False
    assert rec.must_change_password is False
    # Quota convention: 0 ⇒ unlimited.
    assert rec.quota.daily_limit == 0
    assert rec.quota.monthly_limit == 0


def test_is_admin_unaffected_by_new_fields():
    assert UserRecord(uid="u3", role="admin", must_change_password=True).is_admin is True
    assert UserRecord(uid="u4", display_name="x").is_admin is False
