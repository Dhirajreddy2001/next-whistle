#!/usr/bin/env python3
"""Validate generated Next Whistle datasets without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path): return json.loads(path.read_text(encoding="utf-8"))
def sha(path: Path): return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    manifest = load(root / "manifest.json")
    history_path = root / manifest["history"]["file"]
    if sha(history_path) != manifest["history"]["sha256"]: errors.append("history checksum mismatch")
    history = load(history_path)
    if len(history["matches"]) < 24: errors.append("history must contain at least 24 matches")
    if not all(m.get("synthetic") for m in history["matches"]): errors.append("history must be synthetic")
    required = {"goal", "goal_disallowed", "offside", "card_yellow", "foul", "shot"}
    all_kinds = set()
    for entry in manifest["matches"]:
        folder = root / entry["match_id"]
        for name, expected in entry["sha256"].items():
            if sha(folder / f"{name}.json") != expected: errors.append(f"{entry['match_id']} {name} checksum mismatch")
        match, events, snapshots, clips, animation = (load(folder / f"{name}.json") for name in ("match", "events", "snapshots", "clips", "animation"))
        if not 1500 <= len(events) <= 2500: errors.append(f"{entry['match_id']} event count {len(events)}")
        if not 20 <= len(snapshots) <= 40: errors.append(f"{entry['match_id']} snapshot count {len(snapshots)}")
        ids = {e["event_id"] for e in events}; player_ids = {p["player_id"] for t in (match["home"], match["away"]) for p in t["squad"]}; snapshot_ids = {s["snapshot_id"] for s in snapshots}
        starters = {p["player_id"] for t in (match["home"], match["away"]) for p in t["squad"] if p["starter"]}
        for side in (match["home"], match["away"]):
            for player in side["squad"]:
                profile = player.get("visual_profile", {})
                if profile.get("model_id") != "nw_humanoid_v1" or not profile.get("original_synthetic"): errors.append(f"{player['player_id']} invalid visual profile")
        previous = (-1, -1)
        for event in events:
            if (event["period"], event["match_ms"]) < previous: errors.append(f"{entry['match_id']} events not ordered")
            previous = (event["period"], event["match_ms"])
            if event["available_ms"] < event["match_ms"]: errors.append(f"{event['event_id']} available before occurrence")
            if event["player_id"] and event["player_id"] not in player_ids: errors.append(f"{event['event_id']} unknown player")
            for ref in event["related_event_ids"]:
                if ref not in ids: errors.append(f"{event['event_id']} unknown related event")
            for sid in event["snapshot_ids"]:
                if sid not in snapshot_ids: errors.append(f"{event['event_id']} unknown snapshot")
            decision = event.get("decision")
            if decision and not decision.get("geometry_snapshot_id"): errors.append(f"{event['event_id']} decision missing geometry")
            all_kinds.add(event["type"])
        for snapshot in snapshots:
            points = [snapshot["ball"], *snapshot["players"]]
            if len(snapshot["players"]) != 22: errors.append(f"{snapshot['snapshot_id']} needs 22 players")
            if any(not 0 <= p["x"] <= 105 or not 0 <= p["y"] <= 68 for p in points): errors.append(f"{snapshot['snapshot_id']} coordinate out of bounds")
        if not clips or any(c["render_mode"] != "svg_replay" or c["copyright"] != "original_synthetic" for c in clips): errors.append(f"{entry['match_id']} invalid clips")
        animation_by_id = {track["animation_track_id"]: track for track in animation["clips"]}
        for clip in clips:
            track = animation_by_id.get(clip["animation_track_id"])
            if not track or track["sample_rate_hz"] != 10 or not track["synthetic_presentation_only"]: errors.append(f"{clip['clip_id']} invalid animation track"); continue
            if {item["player_id"] for item in track["player_tracks"]} != starters: errors.append(f"{clip['clip_id']} animation must cover 22 starters")
            for player_track in track["player_tracks"]:
                if any(len(sample) != 7 or not 0 <= sample[1] <= 105 or not 0 <= sample[2] <= 68 for sample in player_track["samples"]): errors.append(f"{clip['clip_id']} invalid player animation sample")
            if any(len(sample) != 4 or not 0 <= sample[1] <= 105 or not 0 <= sample[2] <= 68 or sample[3] < 0 for sample in track["ball_track"]): errors.append(f"{clip['clip_id']} invalid ball animation sample")
    missing = required - all_kinds
    if missing: errors.append(f"missing scenario event types: {sorted(missing)}")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path, nargs="?", default=ROOT / "data" / "datasets" / "v1.0.0"); args = parser.parse_args()
    failures = validate(args.root)
    if failures: raise SystemExit("\n".join(failures))
    print(f"valid dataset: {args.root}")
