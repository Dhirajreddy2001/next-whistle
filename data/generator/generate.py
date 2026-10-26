#!/usr/bin/env python3
"""Generate deterministic fictional history and the two Next Whistle demo matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

try:
    from . import movement, phases
except ImportError:  # run as a script
    import movement, phases

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = Path(__file__).with_name("scenarios")
VERSION = "1.3.0"
RULES_VERSION = "1.2.0"
TRACKING_SCHEMA_VERSION = 1
SIMULATION_HZ = 20
BASE_TRACKING_HZ = 4
CLIP_TRACKING_HZ = 20
TRACKING_CHUNK_MS = 60_000
CLIP_KINDS = {"goal", "goal_disallowed", "offside", "penalty_goal", "penalty_saved", "penalty_miss", "tactical_foul_yellow", "red_card", "second_yellow", "free_kick_goal"}
PASS_SPEED_MPS, SHOT_SPEED_MPS = 19.0, 17.0
SEPARATION_M = 1.5
MAX_BALL_STEP_M = 1.5  # per 20 Hz simulation tick, about 30 m/s


def travel_ms(event: dict, gap: int) -> int:
    """Ball flight time scales with distance so short passes are quick and long ones take longer."""
    if event["type"] not in {"pass", "shot"}:
        return gap
    end = event.get("end") or event["start"]
    distance = math.hypot(end["x"] - event["start"]["x"], end["y"] - event["start"]["y"])
    speed = PASS_SPEED_MPS if event["type"] == "pass" else SHOT_SPEED_MPS
    return int(min(gap, max(350, min(2700, distance / speed * 1000))))


BALL_NEUTRAL_TYPES = {"substitution", "card_yellow", "card_red", "foul", "var_check", "period_end", "pressure"}
KEY_EVENT_TYPES = {"goal", "goal_disallowed", "offside", "card_yellow", "card_red", "save"}

TEAMS = {
    "t_rvr": ("Veldmoor FC", "VLD", "#bbf174", "#142c24", ["Ivo Mercer", "Tomas Vale", "Eli Rowan", "Noah Penn", "Dario Moss", "Kian Holt", "Lina Park", "Owen Cade", "Mara Voss", "Sofia Marin", "Rafi Bell", "Jules Reed", "Mina Shaw", "Theo Wren", "Aya North", "Leon Pike"]),
    "t_hrb": ("Tarnhaven Town", "TRN", "#89bfff", "#162639", ["Arlo Dane", "Iris Cove", "Milo Reed", "Nora Finch", "Evan Shore", "Cleo Hart", "Jalen Moss", "Ada Quinn", "Nia Cole", "Leila Okafor", "Remy Ford", "Zara Wynn", "Cal Rook", "Maya Snow", "Omar Lake", "Tess Gray"]),
    "t_ash": ("Quillstone United", "QLS", "#ffb49b", "#3d211c", ["Luca Fern", "Erin Dale", "Niko Ash", "Mira Stone", "Sam Linden", "Ari Grove", "Imani West", "Pia March", "Ezra Lane", "Rina Fox", "Kai Bloom", "Nell Frost", "Hugo Birch", "Sara Muir", "Ben Clay", "Yara Kent"]),
}
POSITIONS = ["GK", "DF", "DF", "DF", "DF", "MF", "MF", "MF", "FW", "FW", "FW", "GK", "DF", "MF", "FW", "MF"]


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dump_compact(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def squad(team_id: str) -> list[dict]:
    names = TEAMS[team_id][4]
    team_offset = sum(ord(char) for char in team_id)
    return [{
        "player_id": f"p_{team_id[2:]}_{i:02d}", "name": name, "shirt": i,
        "position": POSITIONS[i - 1], "starter": i <= 11,
        "movement_profile": {
            "max_speed_mps": round(7.1 + ((i * 11 + team_offset) % 25) / 10, 1),
            "acceleration_mps2": round(2.4 + ((i * 7 + team_offset) % 14) / 10, 1),
            "stamina": round(.72 + ((i * 5 + team_offset) % 25) / 100, 2),
            "work_rate": ["balanced", "high", "balanced", "conservative"][(i + team_offset) % 4],
        },
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
        first_new = len(result)
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
            result.append(base_event(pass_key, at - 1800, "pass", acting["team_id"], passer["player_id"], {"x": 62 if direction_ltr else 43, "y": 31}, target_player_id=attacker["player_id"], end={"x": 84 if direction_ltr else 21, "y": 34}, outcome="complete", qualifiers=["through_ball"]))
            result.append(base_event(key, at, "offside", acting["team_id"], attacker["player_id"], {"x": 84 if direction_ltr else 21, "y": 34}, available_ms=at + moment["review_delay_ms"], outcome="awarded", related_event_ids=[pass_key], decision={"kind": "offside", "decision_type": "offside", "recorded_ruling": "offside", "rule_ids": ["r_law11_offside"], "authored_margin_m": moment["offside_margin_m"], "geometry_snapshot_id": None}))
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
        elif kind in {"shot_miss", "shot_saved"}:
            shooter, forward = players[moment["shooter_shirt"]], 1 if direction_ltr else -1
            box_x = goal_x - 16 * forward
            placement = moment.get("placement", "saved" if kind == "shot_saved" else "wide")
            shot_end = {"wide": {"x": goal_x, "y": 25.5}, "over": {"x": goal_x, "y": 33.0}, "post": {"x": goal_x, "y": 30.3}, "saved": {"x": goal_x, "y": 32.5}}[placement]
            outcome = "saved" if kind == "shot_saved" else "post" if placement == "post" else "off_target"
            related, shot_key = [], f"{key}_shot"
            if "assist_shirt" in moment:
                related = [f"{key}_assist"]
                result.append(base_event(related[0], at - 2400, "pass", acting["team_id"], players[moment["assist_shirt"]]["player_id"], {"x": goal_x - 24 * forward, "y": 22}, target_player_id=shooter["player_id"], end={"x": box_x, "y": 31}, outcome="complete"))
            result.append(base_event(shot_key, at, "shot", acting["team_id"], shooter["player_id"], {"x": box_x, "y": 31}, end=shot_end, outcome=outcome, body_part=moment.get("body_part", "right_foot"), qualifiers={"over": ["over_bar"], "post": ["hit_post"]}.get(placement, []), related_event_ids=related))
            if kind == "shot_saved":
                result.append(base_event(f"{key}_save", at + 900, "save", defending["team_id"], defenders[1]["player_id"], {"x": goal_x, "y": 32.5}, outcome="saved", related_event_ids=[shot_key]))
        elif kind in {"penalty_saved", "penalty_miss"}:
            taker, fouled, offender = players[moment["taker_shirt"]], players[moment["fouled_shirt"]], defenders[moment["offender_shirt"]]
            foul_key, shot_key = f"{key}_foul", f"{key}_shot"
            area_x = 94 if direction_ltr else 11
            goal_line = 104 if direction_ltr else 1
            placement = "saved" if kind == "penalty_saved" else moment.get("placement", "wide")
            shot_end = {"saved": {"x": goal_line, "y": 31}, "wide": {"x": goal_line, "y": 25.5}, "over": {"x": goal_line, "y": 33.0}, "post": {"x": goal_line, "y": 30.3}}[placement]
            outcome = "saved" if placement == "saved" else "post" if placement == "post" else "off_target"
            result += [
                base_event(foul_key, at - 9000, "foul", defending["team_id"], offender["player_id"], {"x": area_x, "y": 38}, target_player_id=fouled["player_id"], outcome="awarded", qualifiers=["penalty"], decision={"kind": "penalty", "decision_type": "penalty", "recorded_ruling": "penalty_awarded", "rule_ids": ["r_law12_promising_attack", "r_law14_penalty_kick"], "geometry_snapshot_id": None}),
                base_event(shot_key, at, "shot", acting["team_id"], taker["player_id"], {"x": area_x, "y": 34}, end=shot_end, outcome=outcome, body_part="right_foot", qualifiers=["penalty", *({"over": ["over_bar"], "post": ["hit_post"]}.get(placement, []))], related_event_ids=[foul_key]),
            ]
            if placement == "saved":
                result.append(base_event(f"{key}_save", at + 900, "save", defending["team_id"], defenders[1]["player_id"], {"x": goal_line, "y": 31}, outcome="saved", qualifiers=["penalty"], related_event_ids=[shot_key]))
        elif kind in {"yellow_card", "red_card", "second_yellow"}:
            offender, target = players[moment["offender_shirt"]], defenders[moment["target_shirt"]]
            spot, reason = {"x": moment.get("x", 55), "y": moment.get("y", 30)}, [moment.get("reason", "reckless")]
            if kind == "second_yellow":
                first_at = moment["first_yellow_ms"]
                result += [
                    base_event(f"{key}_first_foul", first_at, "foul", acting["team_id"], offender["player_id"], spot, target_player_id=target["player_id"], outcome="awarded", qualifiers=["reckless"]),
                    base_event(f"{key}_first_yellow", first_at + 1000, "card_yellow", acting["team_id"], offender["player_id"], spot, outcome="awarded", qualifiers=["first_yellow"], related_event_ids=[f"{key}_first_foul"]),
                ]
            foul_key = f"{key}_foul"
            result.append(base_event(foul_key, at, "foul", acting["team_id"], offender["player_id"], spot, target_player_id=target["player_id"], outcome="awarded", qualifiers=reason))
            if kind == "yellow_card":
                result.append(base_event(key, at + 1000, "card_yellow", acting["team_id"], offender["player_id"], spot, outcome="awarded", qualifiers=reason, related_event_ids=[foul_key]))
            elif kind == "red_card":
                result.append(base_event(key, at + 1000, "card_red", acting["team_id"], offender["player_id"], spot, outcome="awarded", qualifiers=reason, related_event_ids=[foul_key]))
            else:
                yellow_key = f"{key}_yellow"
                result += [
                    base_event(yellow_key, at + 1000, "card_yellow", acting["team_id"], offender["player_id"], spot, outcome="awarded", qualifiers=["second_yellow"], related_event_ids=[foul_key]),
                    base_event(key, at + 1500, "card_red", acting["team_id"], offender["player_id"], spot, outcome="awarded", qualifiers=["second_yellow"], related_event_ids=[yellow_key]),
                ]
        elif kind == "corner":
            taker, target = players[moment["taker_shirt"]], players[moment["target_shirt"]]
            forward = 1 if direction_ltr else -1
            result.append(base_event(key, at, "pass", acting["team_id"], taker["player_id"], {"x": goal_x, "y": 1.0 if moment.get("side", "left") == "left" else 67.0}, target_player_id=target["player_id"], end={"x": goal_x - 6 * forward, "y": 34}, outcome="complete", qualifiers=["corner", "set_piece"]))
            if "clearer_shirt" in moment:
                result.append(base_event(f"{key}_clearance", at + 1800, "clearance", defending["team_id"], defenders[moment["clearer_shirt"]]["player_id"], {"x": goal_x - 6 * forward, "y": 34}, end={"x": goal_x - 30 * forward, "y": 20}, outcome="cleared", related_event_ids=[key]))
        elif kind == "free_kick_goal":
            taker, fouled, offender = players[moment["taker_shirt"]], players[moment["fouled_shirt"]], defenders[moment["offender_shirt"]]
            foul_key, shot_key, goal_key = f"{key}_foul", f"{key}_shot", f"{key}_goal"
            free_kick_x = goal_x - (22 if direction_ltr else -22)
            result += [
                base_event(foul_key, at - 8000, "foul", defending["team_id"], offender["player_id"], {"x": free_kick_x, "y": 30}, target_player_id=fouled["player_id"], outcome="awarded", qualifiers=["free_kick_awarded"]),
                base_event(shot_key, at, "shot", acting["team_id"], taker["player_id"], {"x": free_kick_x, "y": 30}, end={"x": goal_x, "y": 36.5}, outcome="goal", body_part="right_foot", qualifiers=["free_kick", "direct"], related_event_ids=[foul_key]),
                base_event(goal_key, at + 1, "goal", acting["team_id"], taker["player_id"], {"x": goal_x, "y": 36.5}, outcome="awarded", qualifiers=["free_kick"], related_event_ids=[shot_key]),
            ]
        elif kind == "substitution":
            leaving, arriving = players[moment["off_shirt"]], players[moment["on_shirt"]]
            result.append(base_event(key, at, "substitution", acting["team_id"], leaving["player_id"], {"x": 52.5, "y": 0.5}, target_player_id=arriving["player_id"], outcome="completed", qualifiers=moment.get("qualifiers", ["tactical"])))
        elif kind in {"kickoff", "half_time", "full_time"}:
            starter, centre = home["squad"][8], {"x": 52.5, "y": 34}
            if kind == "kickoff":
                result.append(base_event(key, at, "kickoff", home["team_id"], starter["player_id"], centre))
            elif kind == "half_time":
                result += [base_event(f"{key}_end", at - 1, "period_end", home["team_id"], starter["player_id"], centre), base_event(f"{key}_start", at + 1, "period_start", home["team_id"], starter["player_id"], centre)]
            else:
                result.append(base_event(key, at - 1, "period_end", home["team_id"], starter["player_id"], centre))
        for event in result[first_new:]:
            event["_moment"] = key
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


def _formation(team_is_home: bool, period: int) -> list[tuple[float, float]]:
    home_shape = [(5,34),(22,10),(22,27),(22,44),(22,58),(43,15),(43,34),(43,53),(65,12),(68,34),(65,56)]
    away_shape = [(100,34),(83,10),(83,27),(83,44),(83,58),(62,15),(62,34),(62,53),(40,12),(37,34),(40,56)]
    shape = home_shape if team_is_home else away_shape
    if period == 2:
        shape = [(105 - x, y) for x, y in shape]
    return shape


def _ball_at(events: list[dict], index: int, at_ms: int) -> tuple[float, float, float]:
    if index < 0:
        return 52.5, 34.0, 0.0
    event = events[index]
    following = events[index + 1] if index + 1 < len(events) else None
    start = event["start"]
    end = event.get("end") or (following["start"] if following else start)
    gap = max(1, (following["match_ms"] if following else at_ms + 1000) - event["match_ms"])
    flight_ms = travel_ms(event, gap)
    progress = min(1.0, max(0.0, (at_ms - event["match_ms"]) / max(1, flight_ms)))
    eased = progress * progress * (3 - 2 * progress)
    x = start["x"] + (end["x"] - start["x"]) * eased
    y = start["y"] + (end["y"] - start["y"]) * eased
    height = 0.0
    if event["type"] in {"pass", "shot"}:
        height = (1.1 if event["type"] == "pass" else 2.4) * math.sin(math.pi * progress)
    return round(x, 2), round(y, 2), round(max(0.0, height), 2)


def next_involvement(involvement: dict[str, list[tuple[int, dict, str]]], cursor: dict[str, int], player_id: str, at_ms: int) -> tuple[int, dict, str] | None:
    """Next event the player takes part in (as actor or pass target) after at_ms, so players run to the spot ahead of time."""
    rows = involvement.get(player_id, [])
    index = cursor.get(player_id, 0)
    while index < len(rows) and rows[index][0] <= at_ms:
        index += 1
    cursor[player_id] = index
    return rows[index] if index < len(rows) else None


def make_full_tracking(scenario: dict, home: dict, away: dict, events: list[dict], folder: Path) -> dict:
    """Generate deterministic full-match tracking constrained by the event stream."""
    duration = sum(scenario["period_lengths_ms"])
    first_period_end = scenario["period_lengths_ms"][0]
    players = [*home["squad"][:11], *away["squad"][:11]]
    player_order = [player["player_id"] for player in players]
    team_for_player = {player["player_id"]: home["team_id"] if index < 11 else away["team_id"] for index, player in enumerate(players)}
    states: dict[str, list[float]] = {}
    for index, player in enumerate(players):
        x, y = (_formation(index < 11, 1)[index if index < 11 else index - 11])
        states[player["player_id"]] = [x, y, 0.0, 0.0]
    restart_takers = {e["type"]: e["player_id"] for e in reversed(events) if e["type"] == "kickoff"}
    if "kickoff" in restart_takers:
        states[restart_takers["kickoff"]] = [52.5, 34.0, 0.0, 0.0]

    chunks: dict[int, list[list]] = {}
    event_index = -1
    ball_events = [e for e in events if e["type"] not in BALL_NEUTRAL_TYPES]
    ball_index = -1
    step_ms = 1000 // SIMULATION_HZ
    store_every = SIMULATION_HZ // BASE_TRACKING_HZ
    previous_period = 1
    flight_event_id = None
    flight_start = (52.5, 34.0)
    last_ball = (52.5, 34.0)
    flight_cache: dict[str, int] = {}
    involvement: dict[str, list[tuple[int, dict, str]]] = {}
    possession_tags: dict[int, set[str]] = {}
    possession_start: dict[int, int] = {}
    for event in events:
        for pid, role in ((event.get("player_id"), "actor"), (event.get("target_player_id"), "target")):
            if pid:
                involvement.setdefault(pid, []).append((event["match_ms"], event, role))
        possession_tags.setdefault(event["possession_id"], set()).update(event["qualifiers"])
        possession_start[event["possession_id"]] = min(possession_start.get(event["possession_id"], event["match_ms"]), event["match_ms"])
    involvement_cursor = {pid: 0 for pid in involvement}
    squad_states = [states[pid] for pid in player_order]
    team_styles = [phases.STYLES[phases.TEAM_STYLE[home["team_id"]]], phases.STYLES[phases.TEAM_STYLE[away["team_id"]]]]
    for tick, at_ms in enumerate(range(0, duration + 1, step_ms)):
        while event_index + 1 < len(events) and events[event_index + 1]["match_ms"] <= at_ms:
            event_index += 1
        while ball_index + 1 < len(ball_events) and ball_events[ball_index + 1]["match_ms"] <= at_ms:
            ball_index += 1
        period = 1 if at_ms <= first_period_end else 2
        if period != previous_period:
            for index, player in enumerate(players):
                x, y = _formation(index < 11, period)[index if index < 11 else index - 11]
                states[player["player_id"]] = [x, y, 0.0, 0.0]
            second_half_taker = next((e["player_id"] for e in events if e["type"] == "period_start"), None)
            if second_half_taker in states:
                states[second_half_taker] = [52.5, 34.0, 0.0, 0.0]
            previous_period = period

        ball_x, ball_y, ball_z = _ball_at(ball_events, ball_index, at_ms)
        current = events[event_index] if event_index >= 0 else None
        next_event = events[event_index + 1] if event_index + 1 < len(events) else None
        ball_current = ball_events[ball_index] if ball_index >= 0 else None
        ball_next = ball_events[ball_index + 1] if ball_index + 1 < len(ball_events) else None
        possession_team = ball_current["team_id"] if ball_current else home["team_id"]
        event_gap_ms = (ball_next["match_ms"] if ball_next else at_ms + 1000) - ball_current["match_ms"] if ball_current else 0
        flight_ms = 0
        if ball_current and ball_current["type"] in {"pass", "shot"}:
            if ball_current["event_id"] not in flight_cache:
                receiver = states.get(ball_current.get("target_player_id"))
                aim = (receiver[0], receiver[1]) if ball_current["type"] == "pass" and receiver and ball_current.get("outcome") == "complete" else ((ball_current.get("end") or ball_current["start"])["x"], (ball_current.get("end") or ball_current["start"])["y"])
                reach = math.dist(aim, last_ball)
                speed = PASS_SPEED_MPS if ball_current["type"] == "pass" else SHOT_SPEED_MPS
                flight_cache[ball_current["event_id"]] = int(min(max(1, event_gap_ms), max(350, min(2700, reach / speed * 1000))))
            flight_ms = flight_cache[ball_current["event_id"]]
        ball_in_flight = bool(ball_current and ball_current["type"] in {"pass", "shot"} and at_ms - ball_current["match_ms"] < flight_ms)
        attack_side = 0 if possession_team == home["team_id"] else 1
        frames = [(1, 0.0) if (side == 0) == (period == 1) else (-1, 105.0) for side in (0, 1)]
        possession_key = ball_current["possession_id"] if ball_current else 0
        counter_phase = "counter" in possession_tags.get(possession_key, ()) and at_ms - possession_start.get(possession_key, at_ms) < 9000
        closing = sorted(range(1, 11), key=lambda slot: math.hypot(states[player_order[(1 - attack_side) * 11 + slot]][0] - ball_x, states[player_order[(1 - attack_side) * 11 + slot]][1] - ball_y))
        press_rank = {slot: rank for rank, slot in enumerate(closing[:2])}
        for index, player in enumerate(players):
            team_is_home = index < 11
            side, slot = (0, index) if team_is_home else (1, index - 11)
            fwd, own = frames[side]
            style = team_styles[side]
            urgency, target = movement.JOG, None
            if ball_current and ball_current["type"] in {"kickoff", "period_start"} and ball_current.get("player_id") == player["player_id"]:
                target = (52.5, 34.0)
            elif current and current.get("player_id") == player["player_id"] and at_ms - current["match_ms"] <= 1400:
                target = (ball_x, ball_y)
            else:
                upcoming = next_involvement(involvement, involvement_cursor, player["player_id"], at_ms)
                if upcoming and upcoming[0] - at_ms <= (4500 if upcoming[2] == "actor" else 3200):
                    spot = upcoming[1]["start"] if upcoming[2] == "actor" or not upcoming[1].get("end") else upcoming[1]["end"]
                    target = (spot["x"], spot["y"])
                    urgency = movement.SPRINT if math.hypot(target[0] - states[player["player_id"]][0], target[1] - states[player["player_id"]][1]) > 10 else movement.RUN
                elif current and current.get("target_player_id") == player["player_id"] and current.get("end"):
                    target = (current["end"]["x"], current["end"]["y"])
            if target is None:
                ball_u, ball_v = (ball_x - own) * fwd, ball_y
                if side == attack_side:
                    target_u, target_v, urgency = movement.with_ball(slot, ball_u, ball_v, style.line, counter_phase)
                else:
                    target_u, target_v, urgency = movement.without_ball(slot, ball_u, ball_v, style.line, style.press)
                    rank = press_rank.get(slot)
                    if rank is not None and math.hypot(states[player["player_id"]][0] - ball_x, states[player["player_id"]][1] - ball_y) <= movement.presser_distance(style.press, rank):
                        target, urgency = (ball_x - fwd * (3.2 if rank == 0 else 7.0), ball_y), (movement.SPRINT if rank == 0 else movement.RUN)
                if target is None:
                    sway_u, sway_v = movement.sway(slot, at_ms)
                    target = (own + fwd * (target_u + sway_u), target_v + sway_v)
            target_x, target_y = min(103, max(2, target[0])), min(66, max(2, target[1]))
            x, y, vx, vy = states[player["player_id"]]
            dx, dy = target_x - x, target_y - y
            distance = math.hypot(dx, dy)
            profile = player["movement_profile"]
            fatigue = 1 - (at_ms / duration) * (1 - profile["stamina"]) * .38
            max_speed = profile["max_speed_mps"] * fatigue
            desired_speed = min(max_speed * urgency, distance * 1.35)
            desired_vx = dx / distance * desired_speed if distance else 0.0
            desired_vy = dy / distance * desired_speed if distance else 0.0
            max_delta = profile["acceleration_mps2"] / SIMULATION_HZ
            velocity_delta = math.hypot(desired_vx - vx, desired_vy - vy)
            scale = min(1.0, max_delta / velocity_delta) if velocity_delta else 0.0
            vx += (desired_vx - vx) * scale
            vy += (desired_vy - vy) * scale
            x = min(105, max(0, x + vx / SIMULATION_HZ))
            y = min(68, max(0, y + vy / SIMULATION_HZ))
            states[player["player_id"]] = [x, y, vx, vy]

        for first in range(21):  # nobody stands on top of anybody else: players keep at least SEPARATION_M apart
            a = squad_states[first]
            for second in range(first + 1, 22):
                b = squad_states[second]
                dx, dy = b[0] - a[0], b[1] - a[1]
                if -SEPARATION_M < dx < SEPARATION_M and -SEPARATION_M < dy < SEPARATION_M:
                    gap = math.hypot(dx, dy)
                    if gap < SEPARATION_M:
                        push = (SEPARATION_M - gap) / 2 / (gap or 1.0)
                        dx, dy = (dx * push, dy * push) if gap else (SEPARATION_M / 2, 0.0)
                        a[0], a[1], b[0], b[1] = a[0] - dx, a[1] - dy, b[0] + dx, b[1] + dy

        carrier_id = None
        if ball_current and not ball_in_flight:
            if ball_current["type"] == "pass":
                carrier_id = ball_current.get("target_player_id") if ball_current.get("outcome") == "complete" else None
            elif ball_current["type"] not in {"shot", "goal", "goal_disallowed", "kickoff", "period_start"}:
                carrier_id = ball_current.get("player_id")
        if carrier_id in states:
            carrier_x, carrier_y, carrier_vx, carrier_vy = states[carrier_id]
            carrier_speed = math.hypot(carrier_vx, carrier_vy)
            if carrier_speed > .25:
                offset_x, offset_y = carrier_vx / carrier_speed * .7, carrier_vy / carrier_speed * .7
            else:
                carrier_is_home = team_for_player[carrier_id] == home["team_id"]
                attacks_right = carrier_is_home == (period == 1)
                offset_x, offset_y = (.7 if attacks_right else -.7), 0.0
            goal_x = min(105, max(0, carrier_x + offset_x))
            goal_y = min(68, max(0, carrier_y + offset_y))
            gap = math.hypot(goal_x - last_ball[0], goal_y - last_ball[1])
            if gap > MAX_BALL_STEP_M and tick:  # the ball rolls to the new carrier instead of jumping to him
                ball_x, ball_y = last_ball[0] + (goal_x - last_ball[0]) * MAX_BALL_STEP_M / gap, last_ball[1] + (goal_y - last_ball[1]) * MAX_BALL_STEP_M / gap
                ball_z, ball_phase = .15, "flight"
            else:
                ball_x, ball_y, ball_z, ball_phase = goal_x, goal_y, 0.0, "controlled"
        elif ball_in_flight and ball_current:
            if flight_event_id != ball_current["event_id"]:
                flight_event_id = ball_current["event_id"]
                flight_start = last_ball
            receiver = states.get(ball_current.get("target_player_id"))
            destination = (receiver[0], receiver[1]) if ball_current["type"] == "pass" and receiver and ball_current.get("outcome") == "complete" else ((ball_current.get("end") or ball_current["start"])["x"], (ball_current.get("end") or ball_current["start"])["y"])
            flight_progress = min(1.0, max(0.0, (at_ms - ball_current["match_ms"]) / max(1, flight_ms)))
            flight_eased = flight_progress * flight_progress * (3 - 2 * flight_progress)
            ball_x = flight_start[0] + (destination[0] - flight_start[0]) * flight_eased
            ball_y = flight_start[1] + (destination[1] - flight_start[1]) * flight_eased
            ball_z = (1.1 if ball_current["type"] == "pass" else 2.4) * math.sin(math.pi * flight_progress)
            ball_phase = "flight"
        else:
            resting_point = (ball_current.get("end") or ball_current["start"]) if ball_current else {"x": ball_x, "y": ball_y}
            ball_x, ball_y, ball_z = resting_point["x"], resting_point["y"], 0.0
            ball_phase = "dead"

        step = math.hypot(ball_x - last_ball[0], ball_y - last_ball[1])
        if step > MAX_BALL_STEP_M and ball_phase != "controlled":
            ball_x, ball_y = last_ball[0] + (ball_x - last_ball[0]) * MAX_BALL_STEP_M / step, last_ball[1] + (ball_y - last_ball[1]) * MAX_BALL_STEP_M / step
        last_ball = (ball_x, ball_y)
        if tick % store_every == 0 or at_ms == duration:
            flat_players = []
            for player_id in player_order:
                flat_players.extend(round(value, 2) for value in states[player_id])
            possession_code = 0 if possession_team == home["team_id"] else 1
            frame = [at_ms, round(ball_x, 2), round(ball_y, 2), round(ball_z, 2), possession_code, flat_players, ball_phase]
            chunks.setdefault(at_ms // TRACKING_CHUNK_MS, []).append(frame)

    tracking_dir = folder / "tracking"
    chunk_entries = []
    for chunk_index, frames in sorted(chunks.items()):
        filename = f"{chunk_index * 60:04d}-{(chunk_index + 1) * 60:04d}.json"
        path = tracking_dir / filename
        payload = {"schema_version": TRACKING_SCHEMA_VERSION, "from_ms": frames[0][0], "to_ms": frames[-1][0], "frames": frames}
        dump_compact(path, payload)
        chunk_entries.append({"from_ms": frames[0][0], "to_ms": frames[-1][0], "file": f"tracking/{filename}", "sha256": digest(path)})
    manifest = {
        "schema_version": TRACKING_SCHEMA_VERSION,
        "source": "authoritative_synthetic_simulation",
        "simulation_hz": SIMULATION_HZ,
        "base_sample_rate_hz": BASE_TRACKING_HZ,
        "render_interpolation": "cubic_hermite",
        "duration_ms": duration,
        "player_order": player_order,
        "player_sample_columns": ["x", "y", "vx", "vy"],
        "frame_columns": ["match_ms", "ball_x", "ball_y", "ball_z", "possession_team_index", "players_flat", "ball_phase"],
        "chunks": chunk_entries,
    }
    dump(tracking_dir / "manifest.json", manifest)
    return manifest


def make_animation(rng: random.Random, home: dict, away: dict, clips: list[dict], events: list[dict], first_period_end: int) -> dict:
    """Create presentation-only movement tracks; evidence remains in events/snapshots."""
    event_by_id = {event["event_id"]: event for event in events}
    clip_tracks = []
    sample_interval_ms = 1000 // CLIP_TRACKING_HZ
    for clip in clips:
        frames = max(2, (clip["to_ms"] - clip["from_ms"]) // sample_interval_ms + 1)
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
                speed = round(min(9.5, (dx * dx + dy * dy) ** .5 * CLIP_TRACKING_HZ), 2)
                heading = round((__import__("math").degrees(__import__("math").atan2(dy, dx)) + 360) % 360, 1) if speed else 0
                pose = "sprint" if speed >= 6.5 else "jog" if speed >= 2 else "idle"
                if player["player_id"] == target_event.get("player_id") and .48 <= progress <= .58:
                    pose = "shoot" if target_event["type"] in {"shot", "goal"} else "challenge" if target_event["type"] in {"foul", "card_yellow"} else "receive"
                samples.append([index * sample_interval_ms, x, y, heading, speed, pose, round((index % 8) / 8, 3)])
                previous = (x, y)
            tracks.append({"player_id": player["player_id"], "samples": samples})
        start, end = target_event["start"], target_event.get("end") or target_event["start"]
        ball = [[index * sample_interval_ms, round(start["x"] + (end["x"] - start["x"]) * index / (frames - 1), 2), round(start["y"] + (end["y"] - start["y"]) * index / (frames - 1), 2), round(2.2 * __import__("math").sin(__import__("math").pi * index / (frames - 1)), 2)] for index in range(frames)]
        clip_tracks.append({"animation_track_id": clip["animation_track_id"], "clip_id": clip["clip_id"], "sample_rate_hz": CLIP_TRACKING_HZ, "synthetic_presentation_only": True, "source_event_ids": clip["focus_event_ids"], "player_tracks": tracks, "ball_track": ball})
    return {"schema_version": 1, "model_id": "nw_humanoid_v1", "poses": ["idle", "jog", "sprint", "receive", "shoot", "challenge"], "clips": clip_tracks}


def generate_match(scenario: dict, out: Path) -> dict:
    rng = random.Random(scenario["seed"])
    home, away = team(scenario["home"]), team(scenario["away"])
    duration = sum(scenario["period_lengths_ms"])
    events: list[dict] = []
    scripted = scripted_events(scenario, home, away, duration)
    windows = phases.moment_windows(scripted, home["team_id"], BALL_NEUTRAL_TYPES)
    open_play, window_possession = phases.OpenPlay(rng, scenario, (home, away), windows, duration, base_event).run()
    clashes = [e["_key"] for e in open_play if any(w["low"] < e["match_ms"] < w["high"] for w in windows)]
    assert not clashes, f"open play overlaps a scripted moment: {clashes[:3]}"
    events.extend(open_play)
    for event in scripted:  # a scripted moment belongs to the possession that was steered into it
        window = next((w for w in windows if w["low"] <= event["match_ms"] <= w["high"]), None)
        event["possession_id"] = window_possession.get(window["id"], 0) if window else 0
    events.extend(scripted)
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

    anchors = [e for e in events if e.get("decision") or (e["type"] in KEY_EVENT_TYPES and "_moment" in e)]
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
            # Show the moment of the pass: the ball is behind the attacker, the second-last defender
            # holds the line, and the attacker stands exactly the authored margin beyond it.
            line_x = 88.0 if ltr else 17.0
            defending = away if event["team_id"] == home["team_id"] else home
            by_id = {p["player_id"]: p for p in positions}
            keeper, *outfield = [by_id[p["player_id"]] for p in defending["squad"][:11]]
            keeper["x"] = 103.0 if ltr else 2.0
            outfield[0]["x"] = line_x
            for index, defender in enumerate(outfield[1:]):
                defender["x"] = round(line_x - (4 + 2 * index) if ltr else line_x + (4 + 2 * index), 2)
            attacker["x"] = round(line_x + decision["authored_margin_m"] if ltr else line_x - decision["authored_margin_m"], 2)
            snapshot_ball = {"x": line_x - 22 if ltr else line_x + 22, "y": 34}
        else:
            snapshot_ball = event["start"]
        snapshot = {"snapshot_id": sid, "match_ms": event["match_ms"], "available_ms": event["match_ms"], "ball": snapshot_ball, "players": positions, "source_event_ids": [event["event_id"]]}
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
    state = {"score": score, "yellow_cards": sum(e["type"] == "card_yellow" for e in events), "red_cards": sum(e["type"] == "card_red" for e in events), "last_event_id": events[-1]["event_id"]}
    final_hash = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    match = {"match_id": scenario["match_id"], "kickoff_date": scenario["kickoff_date"], "home": home, "away": away, "periods": [{"period": 1, "start_match_ms": 0, "end_match_ms": scenario["period_lengths_ms"][0], "home_attack_direction": "ltr"}, {"period": 2, "start_match_ms": scenario["period_lengths_ms"][0], "end_match_ms": duration, "home_attack_direction": "rtl"}], "venue": scenario["venue"], "final_state_hash": final_hash}
    clips = []
    for moment in (m for m in scenario["moments"] if m.get("clip", m["kind"] in CLIP_KINDS)):
        related = [e["event_id"] for e in events if e.get("_moment") == moment["id"]]
        clip_id = f"clip_{scenario['match_id']}_{moment['id']}"
        clips.append({"clip_id": clip_id, "kind": moment["kind"], "from_ms": max(0, moment["at_ms"] - 12000), "to_ms": min(duration, moment["at_ms"] + 10000), "focus_event_ids": related, "render_mode": "svg_replay", "animation_file": "animation.json", "animation_track_id": f"anim_{clip_id}", "copyright": "original_synthetic"})
    for event in events:
        event.pop("_moment", None)
    folder = out / scenario["match_id"]
    animation = make_animation(rng, home, away, clips, events, scenario["period_lengths_ms"][0])
    make_full_tracking(scenario, home, away, events, folder)
    dump(folder / "match.json", match); dump(folder / "events.json", events); dump(folder / "snapshots.json", snapshots); dump(folder / "clips.json", clips); dump(folder / "animation.json", animation)
    paths = {name: folder / f"{name}.json" for name in ("match", "events", "snapshots", "clips", "animation")}
    paths["tracking"] = folder / "tracking" / "manifest.json"
    return {"match_id": scenario["match_id"], "kickoff_date": scenario["kickoff_date"], "label": f"{home['name']} v {away['name']}", "files": {name: str(path.relative_to(out)) for name, path in paths.items()}, "sha256": {name: digest(path) for name, path in paths.items()}}


def generate_history(out: Path, seed: int = 9917) -> Path:
    rng = random.Random(seed)
    names = ["Veldmoor FC", "Tarnhaven Town", "Quillstone United", "Corvane Athletic", "Dunmere Rovers", "Halcyon Vale", "Pinewick City", "Thornbury Wanderers"]
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
    catalog = {"dataset_version": VERSION, "matches": []}
    for entry in entries:
        match = json.loads((output / entry["files"]["match"]).read_text(encoding="utf-8"))
        catalog["matches"].append({
            "match_id": entry["match_id"], "label": entry["label"], "kickoff_date": entry["kickoff_date"],
            "home": {"team_id": match["home"]["team_id"], "name": match["home"]["name"], "short_name": match["home"]["short_name"], "colors": [match["home"]["primary_color"], match["home"]["secondary_color"]]},
            "away": {"team_id": match["away"]["team_id"], "name": match["away"]["name"], "short_name": match["away"]["short_name"], "colors": [match["away"]["primary_color"], match["away"]["secondary_color"]]},
        })
    catalog["matches"].sort(key=lambda item: item["kickoff_date"])
    dump(output / "catalog.json", catalog)


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
