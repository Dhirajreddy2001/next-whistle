import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def test_catalog_is_spoiler_free_and_uses_api_names(client):
    response = client.get("/api/matches")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["matches"]) == 2
    assert payload["datasetVersion"] == "0.1.0"
    assert payload["matches"][0]["matchId"] == "m_rvr_hrb"
    assert set(payload["matches"][0]) == {"matchId", "label", "kickoffDate", "home", "away"}
    assert "score" not in response.text.lower()
    assert response.headers["x-request-id"].startswith("req_")


def test_health_is_independent_of_azure(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_unknown_route_returns_problem_json(client):
    response = client.get("/api/missing")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["requestId"] == response.headers["x-request-id"]


def test_only_configured_origin_receives_cors_headers(client):
    allowed = client.get("/api/matches", headers={"Origin": "http://localhost:5173"})
    blocked = client.get("/api/matches", headers={"Origin": "https://example.org"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in blocked.headers


def test_invalid_catalog_fails_startup(tmp_path: Path):
    path = tmp_path / "matches.json"
    path.write_text(json.dumps({"dataset_version": "0.1.0", "matches": [{"score": 2}]}))
    with pytest.raises(ValueError), TestClient(create_app(Settings(catalog_path=path))):
        pass
