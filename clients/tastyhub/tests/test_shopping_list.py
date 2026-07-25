"""POST /v1/shopping-list/build — identity-*aware*, never identity-*required*.

The contract this file protects (STEP 44 + STEP 51):
  • an anonymous, API-key-only caller keeps working exactly as before;
  • a caller who supplies a token AND subtract_pantry=true gets the pantry deducted;
  • anything that goes wrong on the pantry path degrades to the plain list rather
    than failing the request.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cookbot.models.shopping import ShoppingItem, ShoppingList
from cookbot.models.spizarnia import Spizarnia, SpizarniaItem
from fastapi.testclient import TestClient

from app.main import app

_UID = "user-shopping-test"
_AUTH = {"authorization": "Bearer valid.token"}

_ORGANIZED = ShoppingList(
    items=[
        ShoppingItem(name="mąka", quantity="500 g", section="produkty suche/sypkie"),
        ShoppingItem(name="cukier", quantity="200 g", section="produkty suche/sypkie"),
    ],
    sections=["produkty suche/sypkie"],
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def stub_agent():
    """The ShoppingListAgent is an LLM call — never make a real one in the unit
    tier. It returns a fixed organized list regardless of input."""
    class _StubAgent:
        async def run(self, _raw: str, **_kw):  # noqa: ANN202
            return MagicMock(output=_ORGANIZED)

    with patch("app.api.shopping_list.build_shopping_list_agent", lambda _c: _StubAgent()):
        yield


def _pantry(*items: tuple[str, str]) -> Spizarnia:
    return Spizarnia(
        uid=_UID,
        items=[SpizarniaItem(name=n, quantity=q, added_at=datetime.now(UTC)) for n, q in items],
    )


def _mock_token():
    return patch(
        "firebase_admin.auth.verify_id_token",
        return_value={"uid": _UID, "email": "test@example.com"},
    )


# ── the anonymous path (STEP 44 contract) ─────────────────────────────────────

def test_anonymous_call_still_works(client):
    """No Authorization header at all — the original contract."""
    resp = client.post("/v1/shopping-list/build", json={"ingredients": ["mąka 500 g"]})
    assert resp.status_code == 200
    assert [i["name"] for i in resp.json()["items"]] == ["mąka", "cukier"]


def test_empty_ingredients_short_circuits(client):
    resp = client.post("/v1/shopping-list/build", json={"ingredients": []})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "sections": []}


def test_anonymous_call_with_subtract_pantry_is_not_an_error(client):
    """The flag without an identity is simply ignored — never a 401."""
    resp = client.post(
        "/v1/shopping-list/build",
        json={"ingredients": ["mąka 500 g"], "subtract_pantry": True},
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


# ── the authenticated pantry path (STEP 51) ───────────────────────────────────

def test_authed_call_without_the_flag_does_not_touch_the_pantry(client):
    """Default False — an authenticated caller who didn't opt in is unaffected,
    and the pantry is never even read."""
    get_spiz = AsyncMock(return_value=_pantry(("mąka", "200 g")))
    with _mock_token(), patch.object(app.state.firestore, "get_spizarnia", new=get_spiz):
        resp = client.post(
            "/v1/shopping-list/build",
            json={"ingredients": ["mąka 500 g"]},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["quantity"] == "500 g"
    get_spiz.assert_not_awaited()


def test_authed_call_with_the_flag_subtracts_the_pantry(client):
    with (
        _mock_token(),
        patch.object(
            app.state.firestore, "get_spizarnia",
            new=AsyncMock(return_value=_pantry(("mąka", "200 g"), ("cukier", "1 kg"))),
        ),
    ):
        resp = client.post(
            "/v1/shopping-list/build",
            json={"ingredients": ["mąka 500 g", "cukier 200 g"], "subtract_pantry": True},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    items = resp.json()["items"]
    # mąka partially covered → reduced; cukier fully covered → dropped.
    assert [i["name"] for i in items] == ["mąka"]
    assert items[0]["quantity"] == "300 g"
    assert items[0]["pantry_note"] != ""


def test_pantry_item_without_a_quantity_is_flagged_not_dropped(client):
    with (
        _mock_token(),
        patch.object(
            app.state.firestore, "get_spizarnia",
            new=AsyncMock(return_value=_pantry(("mąka", ""))),
        ),
    ):
        resp = client.post(
            "/v1/shopping-list/build",
            json={"ingredients": ["mąka 500 g"], "subtract_pantry": True},
            headers=_AUTH,
        )
    items = resp.json()["items"]
    assert len(items) == 2
    assert items[0]["quantity"] == "500 g"
    assert items[0]["pantry_note"] != ""


def test_dev_uid_bypass_also_resolves_an_identity(client):
    """The test frontend runs on the x-dev-uid bypass, so that path must reach
    the pantry too — otherwise subtraction is silently dead in local dev."""
    from app.config.settings import get_settings

    settings = get_settings()
    with (
        patch.object(settings, "dev_uid", _UID),
        patch.object(
            app.state.firestore, "get_spizarnia",
            new=AsyncMock(return_value=_pantry(("mąka", "200 g"))),
        ),
    ):
        resp = client.post(
            "/v1/shopping-list/build",
            json={"ingredients": ["mąka 500 g"], "subtract_pantry": True},
            headers={"x-dev-uid": _UID},
        )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["quantity"] == "300 g"


def test_empty_pantry_returns_the_plain_list(client):
    with (
        _mock_token(),
        patch.object(app.state.firestore, "get_spizarnia",
                     new=AsyncMock(return_value=Spizarnia(uid=_UID))),
    ):
        resp = client.post(
            "/v1/shopping-list/build",
            json={"ingredients": ["mąka 500 g"], "subtract_pantry": True},
            headers=_AUTH,
        )
    assert len(resp.json()["items"]) == 2


# ── degradation (never lose the list) ─────────────────────────────────────────

def test_invalid_token_degrades_to_the_plain_list(client):
    """An unverifiable token means "anonymous" on this route, not 401."""
    with patch("firebase_admin.auth.verify_id_token", side_effect=ValueError("bad token")):
        resp = client.post(
            "/v1/shopping-list/build",
            json={"ingredients": ["mąka 500 g"], "subtract_pantry": True},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


def test_pantry_read_failure_degrades_to_the_plain_list(client):
    with (
        _mock_token(),
        patch.object(app.state.firestore, "get_spizarnia",
                     new=AsyncMock(side_effect=RuntimeError("firestore down"))),
    ):
        resp = client.post(
            "/v1/shopping-list/build",
            json={"ingredients": ["mąka 500 g"], "subtract_pantry": True},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2
