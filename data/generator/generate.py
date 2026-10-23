#!/usr/bin/env python3
"""Generate deterministic fictional history and the two Next Whistle demo matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = Path(__file__).with_name("scenarios")
VERSION = "1.0.0"
RULES_VERSION = "1.1.0"

TEAMS = {
    "t_rvr": ("Riverside FC", "RVR", "#bbf174", "#142c24", ["Ivo Mercer", "Tomas Vale", "Eli Rowan", "Noah Penn", "Dario Moss", "Kian Holt", "Lina Park", "Owen Cade", "Mara Voss", "Sofia Marin", "Rafi Bell", "Jules Reed", "Mina Shaw", "Theo Wren", "Aya North", "Leon Pike"]),
    "t_hrb": ("Harbour Town", "HRB", "#89bfff", "#162639", ["Arlo Dane", "Iris Cove", "Milo Reed", "Nora Finch", "Evan Shore", "Cleo Hart", "Jalen Moss", "Ada Quinn", "Nia Cole", "Leila Okafor", "Remy Ford", "Zara Wynn", "Cal Rook", "Maya Snow", "Omar Lake", "Tess Gray"]),
    "t_ash": ("Ashgrove United", "ASH", "#ffb49b", "#3d211c", ["Luca Fern", "Erin Dale", "Niko Ash", "Mira Stone", "Sam Linden", "Ari Grove", "Imani West", "Pia March", "Ezra Lane", "Rina Fox", "Kai Bloom", "Nell Frost", "Hugo Birch", "Sara Muir", "Ben Clay", "Yara Kent"]),
}
POSITIONS = ["GK", "DF", "DF", "DF", "DF", "MF", "MF", "MF", "FW", "FW", "FW", "GK", "DF", "MF", "FW", "MF"]


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def squad(team_id: str) -> list[dict]:
    names = TEAMS[team_id][4]
    team_offset = sum(ord(char) for char in team_id)
    return [{
        "player_id": f"p_{team_id[2:]}_{i:02d}", "name": name, "shirt": i,
        "position": POSITIONS[i - 1], "starter": i <= 11,
        "visual_profile": {
            "model_id": "nw_humanoid_v1", "height_cm": 166 + ((i * 7 + team_offset) % 24),
            "dominant_foot": "left" if (i + team_offset) % 4 == 0 else "right",
            "body_preset": f"athletic_{chr(97 + (i + team_offset) % 4)}",
            "hair_preset": f"hair_{1 + (i * 3 + team_offset) % 8:02d}",
            "skin_tone_token": f"tone_{1 + (i * 5 + team_offset) % 8:02d}",
            "original_synthetic": True
        }
    } for i, name in enumerate(names, 1)]


def team(team_id: str) -> dict:
    name, short, primary, secondary, _ = TEAMS[team_id]
    return {"team_id": team_id, "name": name, "short_name": short, "primary_color": primary, "secondary_color": secondary, "squad": squad(team_id)}


def load_scenario(path: Path) -> dict:
    # JSON is valid YAML 1.2, so scenario files stay dependency-free.
    return json.loads(path.read_text(encoding="utf-8"))


def point(rng: random.Random, team_is_home: bool, period: int) -> dict:
    ltr = team_is_home == (period == 1)
    x = rng.uniform(7, 98)
    if not ltr:
        x = 105 - x
    return {"x": round(x, 2), "y": round(rng.uniform(3, 65), 2)}


def base_event(key: str, at: int, kind: str, team_id: str, player_id: str | None, start: dict, **extra: object) -> dict:
    event = {"_key": key, "period": 1, "match_ms": at, "available_ms": at, "type": kind, "team_id": team_id, "player_id": player_id, "target_player_id": None, "start": start, "end": None, "outcome": None, "possession_id": 0, "body_part": None, "qualifiers": [], "related_event_ids": [], "snapshot_ids": []}
    event.update(extra)
    return event


def scripted_events(scenario: dict, home: dict, away: dict, duration: int) -> list[dict]:
    result: list[dict] = []
    by_side = {"home": home, "away": away}
    opponent = {"home": away, "away": home}
    for moment in scenario["moments"]:
        side, at, kind, key = moment["team"], moment["at_ms"], moment["kind"], moment["id"]
        acting, defending = by_side[side], opponent[side]
        players = {p["shirt"]: p for p in acting["squad"]}
        defenders = {p["shirt"]: p for p in defending["squad"]}
        direction_ltr = (side == "home") == (at < scenario["period_lengths_ms"][0])
        goal_x = 100 if direction_ltr else 5
        if kind in {"goal", "goal_disallowed"}:
            scorer, assistant = players[moment["scorer_shirt"]], players[moment["assist_shirt"]]
            assist_key, shot_key, goal_key = f"{key}_assist", f"{key}_shot", f"{key}_goal"
            result += [
                base_event(assist_key, at - 2600, "pass", acting["team_id"], assistant["player_id"], {"x": goal_x - (18 if direction_ltr else -18), "y": 28}, target_player_id=scorer["player_id"], end={"x": goal_x - (8 if direction_ltr else -8), "y": 34}, outcome="complete", qualifiers=["through_ball"]),
                base_event(shot_key, at, "shot", acting["team_id"], scorer["player_id"], {"x": goal_x - (8 if direction_ltr else -8), "y": 34}, end={"x": goal_x, "y": 34}, outcome="goal", body_part="right_foot", related_event_ids=[assist_key]),
                base_event(goal_key, at + 1, "goal", acting["team_id"], scorer["player_id"], {"x": goal_x, "y": 34}, outcome="awarded", related_event_ids=[shot_key, assist_key]),
            ]
            if kind == "goal_disallowed":
                review_key = f"{key}_review"
                result.append(base_event(review_key, at + 1000, "goal_disallowed", acting["team_id"], scorer["player_id"], {"x": goal_x - (8 if direction_ltr else -8), "y": 34}, available_ms=at + moment["review_delay_ms"], outcome="not_awarded", related_event_ids=[goal_key], decision={"kind": "offside", "decision_type": "offside", "recorded_ruling": "offside", "reverses_event_id": goal_key, "rule_ids": ["r_law11_offside"], "authored_margin_m": moment["offside_margin_m"], "geometry_snapshot_id": None}))
        elif kind == "offside":
            attacker, passer = players[moment["scorer_shirt"]], players[moment["assist_shirt"]]
            pass_key = f"{key}_pass"
            result.append(base_event(pass_key, at - 1800, "pass", acting["team_id"], passer["player_id"], {"x": 62, "y": 31}, target_player_id=attacker["player_id"], end={"x": 84, "y": 34}, outcome="complete", qualifiers=["through_ball"]))
            result.append(base_event(key, at, "offside", acting["team_id"], attacker["player_id"], {"x": 84, "y": 34}, available_ms=at + moment["review_delay_ms"], outcome="awarded", related_event_ids=[pass_key], decision={"kind": "offside", "decision_type": "offside", "recorded_ruling": "offside", "rule_ids": ["r_law11_offside"], "authored_margin_m": moment["offside_margin_m"], "geometry_snapshot_id": None}))
        elif kind == "tactical_foul_yellow":
            offender, target = players[moment["offender_shirt"]], defenders[moment["target_shirt"]]
            foul_key = f"{key}_foul"
            result.append(base_event(foul_key, at, "foul", acting["team_id"], offender["player_id"], {"x": 60, "y": 36}, target_player_id=target["player_id"], outcome="awarded", qualifiers=["counter", "stopping_promising_attack"]))
            result.append(base_event(key, at + 1000, "card_yellow", acting["team_id"], offender["player_id"], {"x": 60, "y": 36}, outcome="awarded", related_event_ids=[foul_key], decision={"kind": "card", "decision_type": "tactical_foul_yellow", "recorded_ruling": "yellow_card", "rule_ids": ["r_law12_promising_attack"], "geometry_snapshot_id": None}))
        elif kind == "penalty_goal":
            scorer, fouled, offender = players[moment["scorer_shirt"]], players[moment["fouled_shirt"]], defenders[moment["offender_shirt"]]
            foul_key, shot_key, goal_key = f"{key}_foul", f"{key}_shot", f"{key}_goal"
            area_x = 94 if direction_ltr else 11
            result += [
                base_event(foul_key, at - 9000, "foul", defending["team_id"], offender["player_id"], {"x": area_x, "y": 38}, target_player_id=fouled["player_id"], outcome="awarded", qualifiers=["penalty"], decision={"kind": "penalty", "decision_type": "penalty", "recorded_ruling": "penalty_awarded", "rule_ids": ["r_law12_promising_attack", "r_law14_penalty_kick"], "geometry_snapshot_id": None}),
                base_event(shot_key, at, "shot", acting["team_id"], scorer["player_id"], {"x": 94 if direction_ltr else 11, "y": 34}, end={"x": 104 if direction_ltr else 1, "y": 31}, outcome="goal", body_part="right_foot", qualifiers=["penalty"], related_event_ids=[foul_key]),
                base_event(goal_key, at + 1, "goal", acting["team_id"], scorer["player_id"], {"x": 104 if direction_ltr else 1, "y": 31}, outcome="awarded", qualifiers=["penalty"], related_event_ids=[shot_key]),
            ]
    return result


def make_positions(rng: random.Random, home: dict, away: dict) -> list[dict]:
    positions = []
    home_shape = [(5,34),(22,10),(22,27),(22,44),(22,58),(43,15),(43,34),(43,53),(65,12),(68,34),(65,56)]
    away_shape = [(100,34),(83,10),(83,27),(83,44),(83,58),(62,15),(62,34),(62,53),(40,12),(37,34),(40,56)]
    for player, (x, y) in zip(home["squad"][:11], home_shape):
        positions.append({"player_id": player["player_id"], "x": round(x + rng.uniform(-2,2),2), "y": round(y + rng.uniform(-2,2),2)})
    for player, (x, y) in zip(away["squad"][:11], away_shape):
        positions.append({"player_id": player["player_id"], "x": round(x + rng.uniform(-2,2),2), "y": round(y + rng.uniform(-2,2),2)})
    return positions


def make_animation(rng: random.Random, home: dict, away: dict, clips: list[dict], events: list[dict], first_period_end: int) -> dict:
    """Create presentation-only movement tracks; evidence remains in events/snapshots."""
    event_by_id = {event["event_id"]: event for event in events}
    clip_tracks = []
    for clip in clips:
        frames = max(2, (clip["to_ms"] - clip["from_ms"]) // 100 + 1)
        base = {p["player_id"]: p for p in make_positions(rng, home, away)}
        focus_events = [event_by_id[event_id] for event_id in clip["focus_event_ids"]]
        focus_players = {event["player_id"] for event in focus_events if event["player_id"]}
        target_event = focus_events[len(focus_events) // 2]
        tracks = []
        for player in [*home["squad"][:11], *away["squad"][:11]]:
            origin = base[player["player_id"]]
            is_home = player["player_id"].startswith("p_rvr_") if home["team_id"] == "t_rvr" else player["player_id"].startswith(f"p_{home['team_id'][2:]}_")
            ltr = is_home == (clip["from_ms"] < first_period_end)
            phase_seed = rng.random() * 6.283
            samples = []
            previous = (origin["x"], origin["y"])
            for index in range(frames):
                progress = index / (frames - 1)
                forward = (progress - .5) * (7 if ltr else -7)
                x = origin["x"] + forward + 1.4 * __import__("math").sin(progress * 6.283 + phase_seed)
                y = origin["y"] + 1.8 * __import__("math").cos(progress * 6.283 + phase_seed)
                if player["player_id"] in focus_players:
                    pull = max(0, 1 - abs(progress - .55) / .35)
                    x = x * (1 - .72 * pull) + target_event["start"]["x"] * .72 * pull
                    y = y * (1 - .72 * pull) + target_event["start"]["y"] * .72 * pull
                x, y = round(min(105, max(0, x)), 2), round(min(68, max(0, y)), 2)
                dx, dy = x - previous[0], y - previous[1]
                speed = round(min(9.5, (dx * dx + dy * dy) ** .5 * 10), 2)
                heading = round((__import__("math").degrees(__import__("math").atan2(dy, dx)) + 360) % 360, 1) if speed else 0
                pose = "sprint" if speed >= 6.5 else "jog" if speed >= 2 else "idle"
                if player["player_id"] == target_event.get("player_id") and .48 <= progress <= .58:
                    pose = "shoot" if target_event["type"] in {"shot", "goal"} else "challenge" if target_event["type"] in {"foul", "card_yellow"} else "receive"
                samples.append([index * 100, x, y, heading, speed, pose, round((index % 8) / 8, 3)])
                previous = (x, y)
            tracks.append({"player_id": player["player_id"], "samples": samples})
        start, end = target_event["start"], target_event.get("end") or target_event["start"]
        ball = [[index * 100, round(start["x"] + (end["x"] - start["x"]) * index / (frames - 1), 2), round(start["y"] + (end["y"] - start["y"]) * index / (frames - 1), 2), round(2.2 * __import__("math").sin(__import__("math").pi * index / (frames - 1)), 2)] for index in range(frames)]
        clip_tracks.append({"animation_track_id": clip["animation_track_id"], "clip_id": clip["clip_id"], "sample_rate_hz": 10, "synthetic_presentation_only": True, "source_event_ids": clip["focus_event_ids"], "player_tracks": tracks, "ball_track": ball})
    return {"schema_version": 1, "model_id": "nw_humanoid_v1", "poses": ["idle", "jog", "sprint", "receive", "shoot", "challenge"], "clips": clip_tracks}


def generate_match(scenario: dict, out: Path) -> dict:
    rng = random.Random(scenario["seed"])
    home, away = team(scenario["home"]), team(scenario["away"])
    duration = sum(scenario["period_lengths_ms"])
    events: list[dict] = []
    possession, side, block_left = 1, 0, rng.randint(5, 11)
    for i in range(1560):
        at = min(duration - 2, int((i + 1) * duration / 1562) + rng.randint(-500, 500))
        period = 1 if at <= scenario["period_lengths_ms"][0] else 2
        acting = home if side == 0 else away
        roll = rng.random()
        kind = "pass" if roll < .74 else "carry" if roll < .87 else "pressure" if roll < .94 else "shot" if roll < .97 else "recovery"
        actor = rng.choice(acting["squad"][1:11])
        start = point(rng, side == 0, period)
        outcome = "complete" if kind == "pass" and rng.random() < (.83 if side == 0 else .79) else "on_target" if kind == "shot" and rng.random() < .38 else "off_target" if kind == "shot" else "won" if kind == "recovery" else None
        target = rng.choice([p for p in acting["squad"][:11] if p != actor]) if kind == "pass" else None
        end = point(rng, side == 0, period) if kind in {"pass", "carry", "shot"} else None
        events.append(base_event(f"filler_{i:04d}", at, kind, acting["team_id"], actor["player_id"], start, period=period, target_player_id=target["player_id"] if target else None, end=end, outcome=outcome, possession_id=possession))
        block_left -= 1
        if block_left == 0 or (kind == "pass" and outcome != "complete"):
            side, possession, block_left = 1 - side, possession + 1, rng.randint(5, 11)
    for moment in scenario["moments"]:
        events = [e for e in events if abs(e["match_ms"] - moment["at_ms"]) > 12000]
    events.extend(scripted_events(scenario, home, away, duration))
    for event in events:
        event["period"] = 1 if event["match_ms"] <= scenario["period_lengths_ms"][0] else 2
    events.sort(key=lambda e: (e["match_ms"], e["available_ms"], e["_key"]))
    key_to_id = {}
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
        event["event_id"] = f"e_{scenario['match_id']}_{seq:05d}"
        key_to_id[event["_key"]] = event["event_id"]
    for event in events:
        event["related_event_ids"] = [key_to_id.get(x, x) for x in event["related_event_ids"]]
        if event.get("decision", {}).get("reverses_event_id"):
            event["decision"]["reverses_event_id"] = key_to_id[event["decision"]["reverses_event_id"]]

    anchors = sorted(events, key=lambda e: (e["type"] not in {"goal", "goal_disallowed", "offside", "card_yellow", "foul"}, e["match_ms"]))[:12]
    anchors += [min(events, key=lambda e, t=t: abs(e["match_ms"] - t)) for t in range(0, duration, max(1, duration // 12))]
    snapshots = []
    seen = set()
    for event in sorted(anchors, key=lambda e: e["match_ms"]):
        if event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        sid = f"s_{scenario['match_id']}_{len(snapshots)+1:05d}"
        positions = make_positions(rng, home, away)
        decision = event.get("decision")
        if decision and decision["decision_type"] == "offside":
            attacker = next(p for p in positions if p["player_id"] == event["player_id"])
            ltr = (event["team_id"] == home["team_id"]) == (event["period"] == 1)
            attacker["x"] = round((88 + decision["authored_margin_m"]) if ltr else (17 - decision["authored_margin_m"]), 2)
        snapshot = {"snapshot_id": sid, "match_ms": event["match_ms"], "available_ms": event["match_ms"], "ball": event["start"], "players": positions, "source_event_ids": [event["event_id"]]}
        snapshots.append(snapshot)
        event["snapshot_ids"].append(sid)
        if decision:
            decision["geometry_snapshot_id"] = sid

    for event in events:
        event.pop("_key", None)
    visible_goals = [e for e in events if e["type"] == "goal"]
    reversed_ids = {e["decision"]["reverses_event_id"] for e in events if e["type"] == "goal_disallowed"}
    score = {home["team_id"]: 0, away["team_id"]: 0}
    for goal in visible_goals:
        if goal["event_id"] not in reversed_ids:
            score[goal["team_id"]] += 1
    state = {"score": score, "yellow_cards": sum(e["type"] == "card_yellow" for e in events), "last_event_id": events[-1]["event_id"]}
    final_hash = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    match = {"match_id": scenario["match_id"], "kickoff_date": scenario["kickoff_date"], "home": home, "away": away, "periods": [{"period": 1, "start_match_ms": 0, "end_match_ms": scenario["period_lengths_ms"][0], "home_attack_direction": "ltr"}, {"period": 2, "start_match_ms": scenario["period_lengths_ms"][0], "end_match_ms": duration, "home_attack_direction": "rtl"}], "venue": scenario["venue"], "final_state_hash": final_hash}
    clips = []
    for moment in scenario["moments"]:
        related = [e["event_id"] for e in events if abs(e["match_ms"] - moment["at_ms"]) <= 12000]
        clip_id = f"clip_{scenario['match_id']}_{moment['id']}"
        clips.append({"clip_id": clip_id, "kind": moment["kind"], "from_ms": max(0, moment["at_ms"] - 12000), "to_ms": min(duration, moment["at_ms"] + 10000), "focus_event_ids": related, "render_mode": "svg_replay", "animation_file": "animation.json", "animation_track_id": f"anim_{clip_id}", "copyright": "original_synthetic"})
    animation = make_animation(rng, home, away, clips, events, scenario["period_lengths_ms"][0])
    folder = out / scenario["match_id"]
    dump(folder / "match.json", match); dump(folder / "events.json", events); dump(folder / "snapshots.json", snapshots); dump(folder / "clips.json", clips); dump(folder / "animation.json", animation)
    names = ("match", "events", "snapshots", "clips", "animation")
    return {"match_id": scenario["match_id"], "kickoff_date": scenario["kickoff_date"], "label": f"{home['name']} v {away['name']}", "files": {name: f"{scenario['match_id']}/{name}.json" for name in names}, "sha256": {name: digest(folder / f"{name}.json") for name in names}}


def generate_history(out: Path, seed: int = 9917) -> Path:
    rng = random.Random(seed)
    names = ["Riverside FC", "Harbour Town", "Ashgrove United", "Northmere AFC", "Bellweather City", "Cedar Vale", "Marshfield Rovers", "Kingsbridge Athletic"]
    kinds = ["goal", "comeback", "lead_change", "offside", "penalty", "tactical_foul_yellow", "activity_surge"]
    matches = []
    start = date(2025, 8, 2)
    for index in range(36):
        home, away = rng.sample(names, 2)
        hs, as_ = rng.randint(0, 4), rng.randint(0, 4)
        moments = [{"moment_id": f"hm_{index:03d}_{j:02d}", "minute": rng.randint(5, 90), "kind": rng.choice(kinds), "team": rng.choice([home, away]), "zone": rng.choice(["defensive", "middle", "final_third", "penalty_area"]), "score_swing": rng.choice([-1, 0, 1]), "importance": round(rng.uniform(.55, .98), 2)} for j in range(rng.randint(4, 8))]
        matches.append({"history_match_id": f"hm_{index:03d}", "kickoff_date": (start + timedelta(days=index * 6)).isoformat(), "home": home, "away": away, "score": {"home": hs, "away": as_}, "statistics": {"possession_home": rng.randint(38, 62), "passes_home": rng.randint(350, 620), "passes_away": rng.randint(350, 620), "shots_home": rng.randint(6, 20), "shots_away": rng.randint(6, 20), "shots_on_target_home": rng.randint(2, 9), "shots_on_target_away": rng.randint(2, 9)}, "key_moments": sorted(moments, key=lambda m: m["minute"]), "synthetic": True})
    path = out / "history" / "matches.json"
    dump(path, {"schema_version": 1, "history_version": VERSION, "generator_seed": seed, "synthetic": True, "matches": matches})
    return path


def generate_all(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    history = generate_history(output)
    entries = [generate_match(load_scenario(path), output) for path in sorted(SCENARIOS.glob("*.yaml"))]
    manifest = {"schema_version": 1, "dataset_version": VERSION, "rules_version": RULES_VERSION, "rule_edition": "2026/27", "generated_at": "2026-10-22T00:00:00Z", "generator_seed": 4242, "synthetic": True, "history": {"file": "history/matches.json", "sha256": digest(history)}, "matches": entries}
    dump(output / "manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["all", "history", "demo"], nargs="?", default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "datasets" / f"v{VERSION}")
    args = parser.parse_args()
    if args.kind == "history": generate_history(args.output)
    elif args.kind == "demo":
        args.output.mkdir(parents=True, exist_ok=True)
        for file in sorted(SCENARIOS.glob("*.yaml")): generate_match(load_scenario(file), args.output)
    else: generate_all(args.output)
