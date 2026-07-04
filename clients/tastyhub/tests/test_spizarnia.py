from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from cookbot.models.spizarnia import Spizarnia, SpizarniaItem
from cookbot.models.user import UserProfile
from fastapi.testclient import TestClient

from app.main import app

_UID = "user-spiz-test"

_PROFILE = UserProfile(
    uid=_UID,
    display_name="Test",
    email="test@example.com",
    created_at=datetime.now(UTC),
)

_AUTH_HEADER = {"authorization": "Bearer valid.token"}


def _item(name: str, quantity: str = "") -> SpizarniaItem:
    return SpizarniaItem(name=name, quantity=quantity, added_at=datetime.now(UTC))


def _spizarnia(*names: str) -> Spizarnia:
    return Spizarnia(uid=_UID, items=[_item(n) for n in names])


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_auth():
    with patch("app.middleware.auth._get_firebase_app"), patch(
        "firebase_admin.auth.verify_id_token",
        return_value={"uid": _UID, "name": "Test", "email": "test@example.com"},
    ):
        yield


# ---------------------------------------------------------------------------
# GET /v1/spizarnia
# ---------------------------------------------------------------------------

def test_get_spizarnia_empty(client):
    empty = _spizarnia()
    with patch.object(
        app.state.firestore, "get_spizarnia", new=AsyncMock(return_value=empty)
    ):
        resp = client.get("/v1/spizarnia", headers=_AUTH_HEADER)

    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_get_spizarnia_with_items(client):
    stored = _spizarnia("kurczak", "szpinak")
    with patch.object(
        app.state.firestore, "get_spizarnia", new=AsyncMock(return_value=stored)
    ):
        resp = client.get("/v1/spizarnia", headers=_AUTH_HEADER)

    assert resp.status_code == 200
    names = [i["name"] for i in resp.json()["items"]]
    assert "kurczak" in names
    assert "szpinak" in names


# ---------------------------------------------------------------------------
# POST /v1/spizarnia/items
# ---------------------------------------------------------------------------

def test_add_items(client):
    after = _spizarnia("czosnek")
    with (
        patch.object(app.state.firestore, "add_spizarnia_items", new=AsyncMock()),
        patch.object(
            app.state.firestore, "get_spizarnia", new=AsyncMock(return_value=after)
        ),
    ):
        resp = client.post(
            "/v1/spizarnia/items",
            json={"items": [{"name": "czosnek", "quantity": "3 ząbki"}]},
            headers=_AUTH_HEADER,
        )

    assert resp.status_code == 200
    assert resp.json()["items"][0]["name"] == "czosnek"


def test_add_duplicate_updates_quantity(client):
    """add_spizarnia_items merge logic — tested at the Firestore service level."""

    from cookbot.services.firestore import FirestoreService

    svc = FirestoreService.__new__(FirestoreService)
    svc._tenant_id = _UID

    initial = Spizarnia(uid=_UID, items=[_item("kurczak")])

    async def fake_get(uid: str) -> Spizarnia:
        return initial

    async def fake_save(spizarnia: Spizarnia) -> None:
        pass

    import asyncio
    svc.get_spizarnia = fake_get  # type: ignore[assignment]
    svc.save_spizarnia = fake_save  # type: ignore[assignment]

    new_items = [SpizarniaItem(name="Kurczak", quantity="1 kg")]
    asyncio.run(svc.add_spizarnia_items(_UID, new_items))

    # After the call, initial.items should have been mutated — only one kurczak entry
    assert len(initial.items) == 1
    assert initial.items[0].quantity == "1 kg"


# ---------------------------------------------------------------------------
# DELETE /v1/spizarnia/items/{item_name}
# ---------------------------------------------------------------------------

def test_remove_item(client):
    after = _spizarnia("szpinak")
    with (
        patch.object(app.state.firestore, "remove_spizarnia_item", new=AsyncMock()),
        patch.object(
            app.state.firestore, "get_spizarnia", new=AsyncMock(return_value=after)
        ),
    ):
        resp = client.delete("/v1/spizarnia/items/kurczak", headers=_AUTH_HEADER)

    assert resp.status_code == 200
    names = [i["name"] for i in resp.json()["items"]]
    assert "kurczak" not in names


def test_get_spizarnia_requires_auth(client):
    resp = client.get("/v1/spizarnia")
    assert resp.status_code == 401
