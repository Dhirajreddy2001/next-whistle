"""Allow-listed, cutoff-bound deterministic evidence tools."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from next_whistle_shared.contracts.agent import BoundContext, ToolResultEnvelope
from .dataset import EvidenceDataset, EvidenceMatch
from .rule_search import RuleIndex
from .tool_schemas import TOOL_ARGUMENT_MODELS

MAX_RESULT_CHARS = 6000
_ID_PATTERN = re.compile(r"\b(?:e|s|r)_[a-z0-9_]{3,}\b")
MAX_TAG_RULES = 3  # the corpus is ordered with the curated incident rules first


@dataclass
class EvidenceLedger:
    evidence_ids: set[str] = field(default_factory=set)
    stat_values: dict[str, float | int | None] = field(default_factory=dict)
    visible_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    rules: set[str] = field(default_factory=set)
    known_ids: set[str] = field(default_factory=set)  # every event, snapshot or rule id any tool returned
    geometries: dict[str, dict[str, Any]] = field(default_factory=dict)


class ToolDispatcher:
    def __init__(self, context: BoundContext, dataset: EvidenceDataset, ledger: EvidenceLedger | None = None):
        if context.dataset_version != dataset.version:
            raise ValueError("Bound context dataset version mismatch")
        if context.rules_version != dataset.rules_version:
            raise ValueError("Bound context rules version mismatch")
        if context.match_id not in dataset.matches:
            raise ValueError("Bound match is not in the evidence dataset")
        self.context = context
        self.dataset = dataset
        self.match = dataset.matches[context.match_id]
        self.ledger = ledger or EvidenceLedger()
        self.calls = 0
        self._rule_index: RuleIndex | None = None
        match_info = self.match.match
        self._names = {p["player_id"]: p["name"] for side in (match_info["home"], match_info["away"]) for p in side["squad"]}
        self._names.update({side["team_id"]: side["name"] for side in (match_info["home"], match_info["away"])})

    @property
    def events(self) -> list[dict[str, Any]]:
        return [e for e in self.match.events if e["available_ms"] <= self.context.cutoff_ms]

    @property
    def snapshots(self) -> list[dict[str, Any]]:
        return [s for s in self.match.snapshots if s["available_ms"] <= self.context.cutoff_ms]

    def call(self, name: str, arguments: Any) -> ToolResultEnvelope:
        if name not in TOOL_ARGUMENT_MODELS or name not in self.context.allowed_tools:
            return _error("unknown_tool", f"Tool {name!r} is not available to {self.context.agent} for {self.context.intent}.")
        model = TOOL_ARGUMENT_MODELS[name]
        try:
            parsed = model.model_validate(arguments)
        except (ValidationError, TypeError, ValueError) as exc:
            return _error("invalid_arguments", str(exc)[:260])
        if self.calls >= self.context.max_tool_calls:
            return _error("tool_limit_reached", "The agent tool-call limit has been reached.")
        self.calls += 1
        try:
            data = getattr(self, f"_{name}")(**parsed.model_dump())
        except LookupError as exc:
            return _error("not_found", str(exc))
        except PermissionError as exc:
            return _error("not_visible", str(exc))
        except NotImplementedError as exc:
            return _error("unsupported_incident", str(exc))
        except Exception:
            return _error("internal", "Evidence retrieval failed.")
        evidence_ids = _collect_ids(data, "eventId")
        self.ledger.known_ids.update(_ID_PATTERN.findall(json.dumps(data, default=str)))
        stat_refs = _collect_ids(data, "statRef")
        self.ledger.evidence_ids.update(evidence_ids)
        for event in _walk(data):
            if "eventId" in event and "available_ms" in event:
                self.ledger.visible_events[event["eventId"]] = event
            if event.get("statRef"):
                self.ledger.stat_values[event["statRef"]] = event.get("value")
            if event.get("ruleId"):
                self.ledger.rules.add(event["ruleId"])
            if event.get("geometryRef"):
                self.ledger.geometries[event["geometryRef"]] = event
        return _success(data, evidence_ids, stat_refs)

    def _get_match_state(self) -> dict[str, Any]:
        events = self.events
        state_events = sorted(events, key=lambda e: (e["available_ms"], e["seq"]))
        goals = [e for e in state_events if e["type"] == "goal"]
        reversed_ids = {e.get("related_event_ids", [None])[0] for e in state_events if e["type"] == "goal_disallowed"}
        goals = [g for g in goals if g["event_id"] not in reversed_ids]
        match = self.match.match
        home, away = match["home"], match["away"]
        score = {"home": sum(g["team_id"] == home["team_id"] for g in goals), "away": sum(g["team_id"] == away["team_id"] for g in goals)}
        last = state_events[-1] if state_events else None
        return {"matchId": self.context.match_id, "datasetVersion": self.dataset.version,
                "cutoffMs": self.context.cutoff_ms, "score": score,
                "clockMs": last["match_ms"] if last else 0,
                "lastEventId": last["event_id"] if last else None,
                "eventCount": len(state_events)}

    def _get_event_evidence(self, event_id: str, include_build_up: bool, include_snapshots: bool) -> dict[str, Any]:
        events = self.events
        event = next((e for e in events if e["event_id"] == event_id), None)
        if event is None:
            if any(e["event_id"] == event_id for e in self.match.events):
                raise PermissionError("Event is not visible at the bound cutoff.")
            raise LookupError("Event was not found in the bound match.")
        build_up = []
        if include_build_up:
            earlier = [e for e in events if e["possession_id"] == event["possession_id"] and e["seq"] < event["seq"]]
            build_up = earlier[-8:]
        snapshot_ids = set(event.get("snapshot_ids") or [])
        snapshots = [s for s in self.snapshots if s["snapshot_id"] in snapshot_ids] if include_snapshots else []
        self.ledger.evidence_ids.update({event_id, *(e["event_id"] for e in build_up)})
        for visible in [event, *build_up]:
            self.ledger.visible_events[visible["event_id"]] = {
                **visible, "eventId": visible["event_id"], "availableMs": visible["available_ms"],
            }
        return {"event": _public_event(event, self._names), "buildUp": [_public_event(e, self._names, compact=True) for e in build_up[-5:]], "snapshots": snapshots}

    def _get_match_statistics(self, metrics: list[str], scope: str, team_id: str | None) -> dict[str, Any]:
        match = self.match.match
        teams = [match["home"]["team_id"], match["away"]["team_id"]]
        if team_id is not None and team_id not in teams:
            raise LookupError("Team is not in the bound match.")
        selected = [team_id] if team_id else teams
        visible = self.events
        records = []
        cutoff = self.context.cutoff_ms
        period = next((p for p in match["periods"] if p["start_match_ms"] <= cutoff <= p["end_match_ms"]), match["periods"][-1])
        if scope == "window" and cutoff - 600_000 < period["start_match_ms"]:
            return {"scope": "window", "status": "insufficient_data", "statistics": []}
        for tid in selected:
            rows = visible
            suffix = tid
            if scope == "window":
                latest_start = cutoff - 300_000
                previous_start = cutoff - 600_000
                rows = [e for e in visible if previous_start <= e["match_ms"] < cutoff]
                suffix = f"{tid}.latest"
            team_rows = [e for e in rows if e.get("team_id") == tid]
            for metric in metrics:
                latest_rows = [e for e in team_rows if e["match_ms"] >= cutoff - 300_000] if scope == "window" else team_rows
                prior_rows = [e for e in team_rows if cutoff - 600_000 <= e["match_ms"] < cutoff - 300_000] if scope == "window" else []
                value = _metric(metric, latest_rows, rows, tid, teams, match)
                ref = f"{'window' if scope == 'window' else 'team'}.{metric}.{suffix}"
                row = {"metric": metric, "teamId": tid, "scope": scope, "statRef": ref, "value": value}
                if scope == "window":
                    row["previousValue"] = _metric(metric, prior_rows, rows, tid, teams, match)
                    row["previousStatRef"] = f"window.{metric}.{tid}.previous"
                records.append(row)
        return {"statistics": records}

    def _get_player_summary(self, player_id: str) -> dict[str, Any]:
        squads = _squads(self.match.match)
        if player_id not in squads:
            raise LookupError("Player is not in the bound match.")
        rows = [e for e in self.events if e.get("player_id") == player_id or e.get("target_player_id") == player_id]
        recent = rows[-10:]
        return {"player": squads[player_id], "metrics": {m: _metric(m, rows, self.events, None, [], self.match.match, player_id) for m in ("passes_attempted", "passes_completed", "shots", "goals", "touches", "recoveries", "pressures")}, "involvement": [_public_event(e, self._names) for e in recent]}

    def _find_similar_events(self, event_id: str, max_results: int) -> dict[str, Any]:
        visible = self.events
        target = next((e for e in visible if e["event_id"] == event_id), None)
        if target is None:
            if any(e["event_id"] == event_id for e in self.match.events): raise PermissionError("Event is not visible at the bound cutoff.")
            raise LookupError("Event was not found in the bound match.")
        candidates: list[tuple[dict[str, Any], str]] = []
        for match_id, match in self.dataset.matches.items():
            earlier_match = match.kickoff_date < self.match.kickoff_date
            if match_id != self.context.match_id and not earlier_match:
                continue
            rows = match.events if earlier_match else visible
            for candidate in rows:
                if match_id == self.context.match_id and candidate["seq"] >= target["seq"]:
                    continue
                if candidate["event_id"] == event_id or candidate["type"] != target["type"]:
                    continue
                left_kind = (candidate.get("decision") or {}).get("kind")
                right_kind = (target.get("decision") or {}).get("kind")
                if left_kind != right_kind:
                    continue
                candidates.append((candidate, match_id))
        scored = []
        for item, match_id in candidates:
            zmatch = _zone_for_event(item, self.dataset.matches[match_id].match) == _zone_for_event(target, self.match.match)
            q1, q2 = set(item.get("qualifiers", [])), set(target.get("qualifiers", []))
            jaccard = len(q1 & q2) / len(q1 | q2) if q1 | q2 else 1.0
            build_similarity = 1 - min(abs(_build_up_length(item, self.dataset.matches[match_id].events) - _build_up_length(target, self.match.events)) / 8, 1)
            game_match = _game_state_sign(item, self.dataset.matches[match_id].events) == _game_state_sign(target, self.match.events)
            score = .35 * zmatch + .25 * build_similarity + .20 * jaccard + .20 * game_match
            if score >= .55:
                scored.append({"event": _public_event(item, self._names), "matchId": match_id, "score": round(score, 3), "similarities": ["same event type"] + (["same zone"] if zmatch else []), "differences": [] if q1 == q2 else ["qualifiers differ"]})
        return {"matches": sorted(scored, key=lambda x: (-x["score"], -x["event"]["matchMs"]))[:max_results]}

    def _get_rule_reference(self, incident_tag: str) -> dict[str, Any]:
        rows = [r for r in self.dataset.rules if incident_tag in r.get("incident_tags", [])]
        if not rows: raise NotImplementedError("No pinned rule reference supports this incident.")
        return {"rules": [{"ruleId": r["rule_id"], "lawNumber": r["law_number"], "lawTitle": r["law_title"], "summary": r["summary"]} for r in rows[:MAX_TAG_RULES]]}

    def _search_rules(self, query: str, max_results: int) -> dict[str, Any]:
        if self._rule_index is None:
            self._rule_index = RuleIndex(self.dataset.rules)
        hits = self._rule_index.search(query, max_results)
        if not hits: raise LookupError("No pinned rule matches this query.")
        return {"rules": [{"ruleId": r["rule_id"], "edition": r["edition"], "lawNumber": r["law_number"], "lawTitle": r["law_title"], "section": r.get("section"), "summary": r["summary"], "sourceUrl": r["source_url"], "score": score} for score, r in hits]}

    def _get_decision_geometry(self, event_id: str) -> dict[str, Any]:
        event = next((e for e in self.events if e["event_id"] == event_id), None)
        if event is None:
            if any(e["event_id"] == event_id for e in self.match.events): raise PermissionError("Event is not visible at the bound cutoff.")
            raise LookupError("Event was not found in the bound match.")
        decision = event.get("decision", {})
        kind = decision.get("decision_type") or decision.get("kind") or event.get("type")
        if kind not in {"offside", "goal_review", "goal_disallowed", "tactical_foul_yellow", "penalty"}:
            raise NotImplementedError("Event has no supported decision geometry.")
        snapshot_ids = set(event.get("snapshot_ids", []))
        snapshot = next((s for s in self.snapshots if s["snapshot_id"] in snapshot_ids), None)
        if snapshot is None: raise NotImplementedError("No cutoff-visible decision snapshot is recorded.")
        attacker_id = event.get("player_id") or decision.get("attacker_id")
        attacker = next((p for p in snapshot["players"] if p["player_id"] == attacker_id), None)
        if kind == "offside":
            if attacker is None:
                raise NotImplementedError("The decision snapshot does not contain the attacker position.")
            attack_ltr = self._attacks_left_to_right(event)
            norm = lambda x: float(x) if attack_ltr else 105.0 - float(x)
            opponents = [p for p in snapshot["players"] if p["player_id"].split("_")[1] != event["team_id"].split("_")[1]]
            opponents.sort(key=lambda p: norm(p["x"]), reverse=True)
            if len(opponents) < 2: raise NotImplementedError("The snapshot lacks two opponent positions.")
            second_last = opponents[1]
            ball_x = float(snapshot["ball"]["x"])
            margin = round(norm(attacker["x"]) - max(norm(ball_x), norm(second_last["x"])), 2)
            involved = any(
                abs(row["match_ms"] - event["match_ms"]) <= 3000
                and attacker_id in (row.get("player_id"), row.get("target_player_id"))
                for row in self.events
            )
            return {
                "eventId": event_id, "geometryRef": f"geometry.{event_id}", "snapshotId": snapshot["snapshot_id"],
                "decisionType": kind, "attackerId": attacker_id, "attackerName": self._names.get(attacker_id),
                "secondLastDefenderId": second_last["player_id"], "secondLastDefenderName": self._names.get(second_last["player_id"]),
                "marginM": margin, "withinUncertainty": abs(margin) < .10,
                "ownHalf": norm(attacker["x"]) <= 52.5, "involvedInPlay": involved,
                "recordedRuling": decision.get("recorded_ruling", event.get("outcome")),
                "snapshot": {"snapshot_id": snapshot["snapshot_id"], "match_ms": snapshot["match_ms"], "ball": snapshot["ball"]},  # not all 22 positions: they cost tokens the model does not need
            }
        if kind in {"tactical_foul_yellow", "penalty"}:
            point = event.get("start")
            if not point: raise NotImplementedError("The decision has no recorded foul location.")
            attack_ltr = self._attacks_left_to_right(event)
            goal_x = 105.0 if attack_ltr else 0.0
            distance = round(math.hypot(goal_x - point["x"], 34.0 - point["y"]), 2)
            inside = (point["x"] >= 88.5 if attack_ltr else point["x"] <= 16.5) and 13.84 <= point["y"] <= 54.16
            between = [p for p in snapshot["players"] if min(point["x"], goal_x) <= p["x"] <= max(point["x"], goal_x)]
            regain = any(e.get("team_id") == event.get("team_id") and e["type"] in {"recovery", "interception", "tackle"} and 0 <= e["match_ms"] - event["match_ms"] <= 3000 for e in self.events)
            return {
                "eventId": event_id, "geometryRef": f"geometry.{event_id}", "snapshotId": snapshot["snapshot_id"],
                "decisionType": kind, "location": point, "insidePenaltyArea": inside,
                "distanceToGoalM": distance,
                "playersBetween": [{"playerId": p["player_id"], "teamId": "t_" + p["player_id"].split("_")[1]} for p in between],
                "contactQualifiers": event.get("qualifiers", []), "advantagePossible": regain,
                "recordedRuling": decision.get("recorded_ruling", event.get("outcome")), "snapshot": {"snapshot_id": snapshot["snapshot_id"], "match_ms": snapshot["match_ms"], "ball": snapshot["ball"]},  # not all 22 positions: they cost tokens the model does not need
            }
        raise NotImplementedError("Decision type is not supported by the geometry engine.")

    def _attacks_left_to_right(self, event: dict[str, Any]) -> bool:
        period = next((p for p in self.match.match["periods"] if p["start_match_ms"] <= event["match_ms"] <= p["end_match_ms"]), None)
        home_ltr = not period or period.get("home_attack_direction", "ltr") == "ltr"
        return home_ltr if event["team_id"] == self.match.match["home"]["team_id"] else not home_ltr


def _metric(metric: str, rows: list[dict], all_rows: list[dict], team_id: str | None, teams: list[str], match: dict, player_id: str | None = None):
    if player_id:
        rows = [e for e in all_rows if e.get("player_id") == player_id]
    passes = [e for e in rows if e["type"] == "pass" and "kickoff_restart" not in e.get("qualifiers", [])]
    completed = [e for e in passes if e.get("outcome") == "complete"]
    shots = [e for e in rows if e["type"] == "shot"]
    goals = [e for e in all_rows if e["type"] == "goal" and (not team_id or e.get("team_id") == team_id)]
    reversed_ids = {e.get("reverses_event_id") for e in all_rows if e["type"] == "goal_disallowed"}
    goals = [e for e in goals if e["event_id"] not in reversed_ids]
    if metric == "passes_attempted": return len(passes)
    if metric == "passes_completed": return len(completed)
    if metric == "pass_completion_pct": return round(100 * len(completed) / len(passes), 1) if passes else None
    if metric == "avg_pass_distance_m":
        d = [_distance(e) for e in passes if e.get("start") and e.get("end")]
        return round(sum(d) / len(d), 1) if d else None
    if metric == "long_passes": return sum(_distance(e) >= 30 for e in passes if e.get("start") and e.get("end"))
    if metric == "shots": return len(shots)
    if metric == "shots_on_target": return sum(e.get("outcome") in {"on_target", "saved", "goal"} for e in shots)
    if metric == "goals": return len(goals)
    if metric == "final_third_entries": return sum(e["type"] in {"pass", "carry"} and e.get("outcome") == "complete" and e.get("start") and e.get("end") and e["start"]["x"] < 70 <= e["end"]["x"] for e in rows)
    if metric == "touches": return sum(e["type"] not in {"pressure", "foul", "card_yellow", "card_red", "substitution"} for e in rows)
    if metric == "recoveries": return sum(e["type"] in {"recovery", "interception"} or (e["type"] == "tackle" and e.get("outcome") == "won") for e in rows)
    if metric == "pressures": return sum(e["type"] == "pressure" for e in rows)
    if metric == "possession_pct":
        team_time = total = 0
        by_pos = {}
        ordered = sorted(all_rows, key=lambda e: e["match_ms"])
        for i, e in enumerate(ordered[:-1]):
            duration = max(0, ordered[i+1]["match_ms"] - e["match_ms"])
            total += duration
            if e.get("team_id") == team_id: team_time += duration
        return round(100 * team_time / total, 1) if total else 50.0
    return None


def _public_event(event: dict, names: dict[str, str] | None = None, compact: bool = False) -> dict:
    """The event as the agents see it. Names are included so a model never has to repeat raw IDs, and generator-internal
    fields (the authored margin) are left out: measurements come from get_decision_geometry only."""
    names = names or {}
    decision = event.get("decision")
    if decision:
        decision = {key: value for key, value in decision.items() if key != "authored_margin_m"}
    if compact:  # context events: enough to tell the story, nothing the model would be tempted to quote as a number
        return {
            "eventId": event["event_id"], "type": event["type"], "matchMs": event["match_ms"], "teamName": names.get(event.get("team_id") or ""),
            "playerName": names.get(event.get("player_id") or ""), "targetPlayerName": names.get(event.get("target_player_id") or ""),
            "outcome": event.get("outcome"), "decisionType": (decision or {}).get("decision_type"),
        }
    return {
        "eventId": event["event_id"], "type": event["type"], "matchMs": event["match_ms"], "availableMs": event["available_ms"],
        "teamId": event.get("team_id"), "teamName": names.get(event.get("team_id") or ""),
        "playerId": event.get("player_id"), "playerName": names.get(event.get("player_id") or ""),
        "targetPlayerId": event.get("target_player_id"), "targetPlayerName": names.get(event.get("target_player_id") or ""),
        "outcome": event.get("outcome"), "qualifiers": event.get("qualifiers", []),
        "relatedEventIds": event.get("related_event_ids", []), "decision": decision,
    }


def _squads(match: dict) -> dict[str, dict]:
    return {p["player_id"]: {"playerId": p["player_id"], "name": p["name"], "shirt": p["shirt"], "position": p["position"], "teamId": side["team_id"]} for side in (match["home"], match["away"]) for p in side["squad"]}


def _zone_for_event(event: dict, match: dict):
    point = event.get("start")
    if not point: return None
    period = next((p for p in match["periods"] if p["start_match_ms"] <= event["match_ms"] <= p["end_match_ms"]), None)
    home_ltr = not period or period.get("home_attack_direction", "ltr") == "ltr"
    attacks_ltr = home_ltr if event.get("team_id") == match["home"]["team_id"] else not home_ltr
    x = point["x"] if attacks_ltr else 105 - point["x"]
    return (min(2, int(x // 35)), min(5, int(point["y"] // (68 / 6))))
def _distance(e): return math.hypot(e["end"]["x"] - e["start"]["x"], e["end"]["y"] - e["start"]["y"])
def _build_up_length(event: dict, rows: tuple[dict, ...]) -> int:
    preceding = [e for e in rows if e["possession_id"] == event["possession_id"] and e["seq"] < event["seq"]]
    return min(8, sum(e["type"] == "pass" for e in preceding[-8:]))
def _game_state_sign(event: dict, rows: tuple[dict, ...]) -> int:
    visible = [e for e in rows if e["available_ms"] <= event["available_ms"]]
    reversals = {e.get("decision", {}).get("reverses_event_id") for e in visible if e["type"] == "goal_disallowed"}
    # Compare the acting team's score difference; zero is a distinct state.
    goals = [e for e in visible if e["type"] == "goal" and e["event_id"] not in reversals]
    team_score = sum(e.get("team_id") == event.get("team_id") for e in goals)
    opposition_score = sum(e.get("team_id") != event.get("team_id") for e in goals)
    diff = team_score - opposition_score
    return (diff > 0) - (diff < 0)
def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values(): yield from _walk(item)
    elif isinstance(value, list):
        for item in value: yield from _walk(item)
def _collect_ids(value, key): return sorted({str(item[key]) for item in _walk(value) if item.get(key)})
def _error(code, message): return ToolResultEnvelope(ok=False, error={"code": code, "message": message})
def _success(data, evidence, stats):
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    note = None
    if len(serialized) > MAX_RESULT_CHARS:
        serialized = serialized[:MAX_RESULT_CHARS - 30]
        data = {"truncated": serialized, "originalChars": len(json.dumps(data, ensure_ascii=False))}
        note = f"Result truncated to {MAX_RESULT_CHARS} serialized characters."
    return ToolResultEnvelope(ok=True, data=data, evidence_ids=evidence[:64], stat_refs=stats[:32], note=note)
