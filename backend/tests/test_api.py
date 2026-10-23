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


def test_live_state_is_shared_and_wall_clock_driven():
    settings = Settings(
        catalog_path=Path(__file__).resolve().parents[2] / "data/catalog/matches.json",
        dataset_path=Path(__file__).resolve().parents[2] / "data/datasets/v1.1.0",
        live_instance_epoch_ms=1_000_000,
        live_match_duration_ms=5_760_000,
    )
    with TestClient(create_app(settings, now_ms=lambda: 1_000_000 + 2_345_678)) as first:
        first_state = first.get("/api/live-state").json()
    with TestClient(create_app(settings, now_ms=lambda: 1_000_000 + 2_345_678)) as second:
        second_state = second.get("/api/live-state").json()
    assert first_state == second_state
    assert first_state["matchMs"] == 2_345_678
    assert first_state["instanceId"] == "li_rvr_hrb_0"
    with TestClient(create_app(settings, now_ms=lambda: 1_000_000 + 2_345_678)) as client:
        assert client.get("/api/live-state").headers["cache-control"] == "no-store"


def test_tracking_is_chunked_and_cacheable(client):
    manifest = client.get("/api/matches/m_rvr_hrb/tracking/manifest")
    chunk = client.get("/api/matches/m_rvr_hrb/tracking/chunks/0")
    assert manifest.status_code == 200
    assert manifest.json()["base_sample_rate_hz"] == 2
    assert chunk.status_code == 200
    assert chunk.json()["frames"][0][0] == 0
    assert "immutable" in chunk.headers["cache-control"]
    assert client.get("/api/matches/missing/tracking/chunks/0").status_code == 404


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
    dataset_path = Path(__file__).resolve().parents[2] / "data/datasets/v1.1.0"
    with pytest.raises(ValueError), TestClient(create_app(Settings(catalog_path=path, dataset_path=dataset_path))):
        pass
