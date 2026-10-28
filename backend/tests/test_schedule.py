from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from next_whistle_core.config import Settings
from next_whistle_core.main import create_app
from next_whistle_core.services.schedule import HALF_TIME_MS, build_schedule, playable_for_slot, slot_of, window_offset_ms

ROOT = Path(__file__).resolve().parents[2]
EPOCH, DURATION = 1_000_000, 5_760_000
TEAM = {"team_id": "t_x", "name": "X FC", "short_name": "XFC", "colors": ["#111111", "#eeeeee"]}
MATCHES = [{"match_id": "m_a", "home": {**TEAM, "name": "Alpha"}, "away": {**TEAM, "name": "Beta"}}, {"match_id": "m_b", "home": {**TEAM, "name": "Gamma"}, "away": {**TEAM, "name": "Delta"}}]
HISTORY = [{"history_match_id": f"hm_{i:03d}", "home": f"Home {i}", "away": f"Away {i}", "score": {"home": i % 4, "away": i % 3}, "key_moments": [{"minute": 10 + i, "kind": "goal", "team": f"Home {i}", "importance": 0.5}]} for i in range(12)]


def schedule(now_ms: int, **options):
    return build_schedule(now_ms, epoch_ms=EPOCH, duration_ms=DURATION, matches=MATCHES, history=HISTORY, **options)


def test_everyone_gets_the_same_sequence():
    assert schedule(EPOCH + 40 * DURATION + 5) == schedule(EPOCH + 40 * DURATION + 5)
    first, later = schedule(EPOCH + 40 * DURATION), schedule(EPOCH + 41 * DURATION)
    assert [item["slot"] for item in later["items"][1:4]] == [40, 39, 38]
    assert later["items"][1]["match_id"] == first["items"][0]["match_id"]  # last slot's live match is now a replay of itself


def test_live_then_replays_then_results_newest_first():
    items = schedule(EPOCH + 40 * DURATION + 1234)["items"]
    assert [item["kind"] for item in items] == ["live"] + ["replay"] * 5 + ["result"] * 30
    assert [item["slot"] for item in items] == list(range(40, 40 - len(items), -1))
    assert items[0]["live_match_ms"] == window_offset_ms(40) + 1234 and items[1].get("live_match_ms") is None
    assert all("match_id" not in item for item in items if item["kind"] == "result")


def test_start_times_are_utc_slot_boundaries():
    items = schedule(EPOCH + 3 * DURATION)["items"]
    assert items[0]["starts_at_ms"] == EPOCH + 3 * DURATION and items[1]["starts_at_ms"] == EPOCH + 2 * DURATION
    assert items[0]["starts_at"] == datetime.fromtimestamp((EPOCH + 3 * DURATION) // 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_every_playable_match_runs_once_per_round():
    for round_start in (0, 2, 10, 100):
        assert sorted(playable_for_slot(slot, ["m_a", "m_b"]) for slot in (round_start, round_start + 1)) == ["m_a", "m_b"]


def test_results_use_many_teams_and_never_repeat_inside_the_history():
    results = [item for item in schedule(EPOCH + 90 * DURATION, results=12)["items"] if item["kind"] == "result"]
    assert len({item["home"]["name"] for item in results}) == 12
    assert all(item["score"] is not None and item["key_moments"] for item in results)


def test_early_slots_do_not_go_before_the_first_slot():
    assert [item["slot"] for item in schedule(EPOCH + 2 * DURATION)["items"]] == [2, 1, 0]
    assert slot_of(0, EPOCH, DURATION) == 0


def test_schedule_endpoint_matches_live_state(tmp_path: Path):
    settings = Settings(
        catalog_path=ROOT / "data/catalog/matches.json", dataset_path=ROOT / "data/datasets/v1.3.0",
        live_instance_epoch_ms=EPOCH, live_match_duration_ms=DURATION, session_db_path=tmp_path / "sessions.sqlite3", session_signing_key="test-signing-key-that-is-long-enough",
    )
    now = EPOCH + 50 * DURATION + 600_000
    with TestClient(create_app(settings, now_ms=lambda: now)) as client:
        response = client.get("/api/schedule")
        body, live = response.json(), client.get("/api/live-state").json()
    assert body["items"][0]["kind"] == "live" and body["items"][0]["matchId"] == live["matchId"]
    assert body["items"][0]["liveMatchMs"] == live["matchMs"] == window_offset_ms(50) + 600_000
    assert len({item["home"]["name"] for item in body["items"]} | {item["away"]["name"] for item in body["items"]}) >= 8
    assert response.headers["cache-control"] == "no-store"
    assert all(item["score"] is not None for item in body["items"])  # live, replays and results all show a score
    assert body["items"][1]["kind"] == "replay" and sum(body["items"][1]["score"].values()) >= 0


def test_only_the_live_match_is_held_back_in_tracking(tmp_path: Path):
    settings = Settings(
        catalog_path=ROOT / "data/catalog/matches.json", dataset_path=ROOT / "data/datasets/v1.3.0",
        live_instance_epoch_ms=EPOCH, live_match_duration_ms=DURATION, session_db_path=tmp_path / "sessions.sqlite3", session_signing_key="test-signing-key-that-is-long-enough",
    )
    with TestClient(create_app(settings, now_ms=lambda: EPOCH + 50 * DURATION + 600_000)) as client:
        live = client.get("/api/live-state").json()["matchId"]
        other = next(item["matchId"] for item in client.get("/api/matches").json()["matches"] if item["matchId"] != live)
        assert client.get(f"/api/matches/{live}/tracking/chunks/95").status_code == 404  # minute 95: the live match has not got there yet
        assert client.get(f"/api/matches/{other}/tracking/chunks/95", params={"cutoffMs": 5_700_000}).status_code == 200  # a replay has


def test_live_window_always_opens_after_half_time_and_stays_inside_the_match():
    offsets = [window_offset_ms(slot) for slot in range(500)]
    assert min(offsets) > HALF_TIME_MS and max(offsets) < HALF_TIME_MS + 21 * 60_000
    assert max(offsets) + 1_800_000 <= 5_760_000
    assert len(set(offsets)) > 100 and window_offset_ms(7) == window_offset_ms(7)
