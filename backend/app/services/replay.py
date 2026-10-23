"""Deterministic match facts derived from generated source data."""

from __future__ import annotations

from collections import Counter

from app.store.datasets import MatchData, tracking_frames


def visible_events(data: MatchData, cutoff_ms: int) -> list[dict]:
    return [event for event in data.events if event["available_ms"] <= cutoff_ms]


def state_at(data: MatchData, cutoff_ms: int) -> dict:
    visible = visible_events(data, cutoff_ms)
    reversed_goals = {
        event.get("decision", {}).get("reverses_event_id")
        for event in visible
        if event.get("decision", {}).get("reverses_event_id")
    }
    home_id, away_id = data.match["home"]["team_id"], data.match["away"]["team_id"]
    score = {home_id: 0, away_id: 0}
    for event in visible:
        if event["type"] == "goal" and event["event_id"] not in reversed_goals:
            score[event["team_id"]] += 1
    period = next((item["period"] for item in data.match["periods"] if cutoff_ms < item["end_match_ms"]), len(data.match["periods"]))
    return {
        "match_id": data.match["match_id"],
        "cutoff_ms": cutoff_ms,
        "score": score,
        "period": period,
        "last_event_id": visible[-1]["event_id"] if visible else None,
        "status": "full_time" if cutoff_ms >= data.duration_ms else "live",
    }


def team_stats(data: MatchData, cutoff_ms: int) -> dict:
    visible = visible_events(data, cutoff_ms)
    match = data.match
    possessions = [0, 0]
    for frame in tracking_frames(data, cutoff_ms):
        if frame[0] < cutoff_ms and frame[6] != "dead" and frame[4] in (0, 1):
            possessions[frame[4]] += 1
    possession_total = sum(possessions)
    reversed_goals = {
        event.get("decision", {}).get("reverses_event_id")
        for event in visible
        if event.get("decision", {}).get("reverses_event_id")
    }
    reversed_goal_shots = {
        related_id
        for event in visible
        if event["event_id"] in reversed_goals
        for related_id in event.get("related_event_ids", [])
    }
    teams = []
    for team_index, team in enumerate((match["home"], match["away"])):
        events = [event for event in visible if event["team_id"] == team["team_id"]]
        shots = [event for event in events if event["type"] == "shot"]
        passes = [event for event in events if event["type"] == "pass" and "kickoff_restart" not in event.get("qualifiers", [])]
        on_target = sum(event.get("outcome") in {"goal", "saved", "on_target"} and event["event_id"] not in reversed_goal_shots for event in shots)
        completed = sum(event.get("outcome") == "complete" for event in passes)
        pass_distances = [
            ((event["end"]["x"] - event["start"]["x"]) ** 2 + (event["end"]["y"] - event["start"]["y"]) ** 2) ** 0.5
            for event in passes if event.get("end")
        ]
        long_passes = sum(distance >= 30 for distance in pass_distances)
        final_third_entries = 0
        for event in events:
            if event["type"] not in {"pass", "carry"} or event.get("outcome") != "complete" or not event.get("end"):
                continue
            period = next((item for item in match["periods"] if item["period"] == event["period"]), None)
            team_attacks_ltr = period["home_attack_direction"] == "ltr" if team_index == 0 else period["home_attack_direction"] != "ltr"
            start_x = event["start"]["x"] if team_attacks_ltr else 105 - event["start"]["x"]
            end_x = event["end"]["x"] if team_attacks_ltr else 105 - event["end"]["x"]
            final_third_entries += start_x < 70 <= end_x
        teams.append({
            "team_id": team["team_id"],
            "shots": len(shots),
            "shots_on_target": on_target,
            "passes": len(passes),
            "passes_completed": completed,
            "pass_completion_pct": round(completed / len(passes) * 100, 1) if passes else None,
            "average_pass_distance_m": round(sum(pass_distances) / len(pass_distances), 1) if pass_distances else None,
            "long_passes": long_passes,
            "final_third_entries": final_third_entries,
            "possession_pct": round(possessions[team_index] / possession_total * 100, 1) if possession_total else 50.0,
            "goals": state_at(data, cutoff_ms)["score"][team["team_id"]],
            "yellow_cards": sum(event["type"] == "card_yellow" for event in events),
            "red_cards": sum(event["type"] == "card_red" for event in events),
        })
    types = Counter(event["type"] for event in visible)
    return {"cutoff_ms": cutoff_ms, "teams": teams, "event_counts": dict(types)}


def player_summary(data: MatchData, player_id: str, cutoff_ms: int) -> dict | None:
    team = next((side for side in (data.match["home"], data.match["away"]) if any(p["player_id"] == player_id for p in side["squad"])), None)
    if team is None:
        return None
    player = next(p for p in team["squad"] if p["player_id"] == player_id)
    events = [event for event in visible_events(data, cutoff_ms) if event.get("player_id") == player_id]
    return {
        "player_id": player_id,
        "team_id": team["team_id"],
        "name": player["name"],
        "shirt": player["shirt"],
        "position": player["position"],
        "starter": player["starter"],
        "cutoff_ms": cutoff_ms,
        "event_counts": dict(Counter(event["type"] for event in events)),
        "event_ids": [event["event_id"] for event in events],
    }
