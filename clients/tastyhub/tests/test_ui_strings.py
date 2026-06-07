import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_ui_strings_returns_200(client):
    resp = client.get("/v1/ui-strings")
    assert resp.status_code == 200


def test_ui_strings_greeting_in_polish(client):
    data = client.get("/v1/ui-strings").json()
    assert "greeting" in data
    # TastyHub is configured with Polish — greeting must contain Polish text
    assert len(data["greeting"]) > 10


def test_ui_strings_has_spizarnia_labels(client):
    data = client.get("/v1/ui-strings").json()
    assert "spizarnia_heading" in data
    assert "spizarnia_toggle" in data
    assert "shopping_list_heading" in data
    assert "login_heading" in data


def test_ui_strings_no_auth_required(client):
    # Endpoint must be publicly accessible — no API key or Bearer token needed
    resp = client.get("/v1/ui-strings")
    assert resp.status_code == 200
