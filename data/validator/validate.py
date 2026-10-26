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
    if manifest["dataset_version"] >= "1.2.0": required |= {"card_red", "save", "substitution", "kickoff", "period_end", "period_start", "clearance"}
    all_kinds = set()
    for entry in manifest["matches"]:
        folder = root / entry["match_id"]
        for name, expected in entry["sha256"].items():
            if sha(root / entry["files"][name]) != expected: errors.append(f"{entry['match_id']} {name} checksum mismatch")
        match, events, snapshots, clips, animation = (load(folder / f"{name}.json") for name in ("match", "events", "snapshots", "clips", "animation"))
        tracking = load(root / entry["files"]["tracking"])
        if not 1500 <= len(events) <= 3500: errors.append(f"{entry['match_id']} event count {len(events)}")
        if not 20 <= len(snapshots) <= 40: errors.append(f"{entry['match_id']} snapshot count {len(snapshots)}")
        ids = {e["event_id"] for e in events}; player_ids = {p["player_id"] for t in (match["home"], match["away"]) for p in t["squad"]}; snapshot_ids = {s["snapshot_id"] for s in snapshots}
        starters = {p["player_id"] for t in (match["home"], match["away"]) for p in t["squad"] if p["starter"]}
        for side in (match["home"], match["away"]):
            for player in side["squad"]:
                profile = player.get("visual_profile", {})
                if profile.get("model_id") != "nw_humanoid_v1" or not profile.get("original_synthetic"): errors.append(f"{player['player_id']} invalid visual profile")
                movement = player.get("movement_profile", {})
                if not 6 <= movement.get("max_speed_mps", 0) <= 10 or not 2 <= movement.get("acceleration_mps2", 0) <= 5: errors.append(f"{player['player_id']} invalid movement profile")
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
        key_events = {"goal", "goal_disallowed", "offside", "card_yellow", "card_red", "save"}
        later_match_start = max(50 * 60 * 1000, match["periods"][1]["start_match_ms"])
        if not any(event["type"] in key_events and event["match_ms"] >= later_match_start for event in events):
            errors.append(f"{entry['match_id']} needs at least one key moment after minute 50")
        for snapshot in snapshots:
            points = [snapshot["ball"], *snapshot["players"]]
            if len(snapshot["players"]) != 22: errors.append(f"{snapshot['snapshot_id']} needs 22 players")
            if any(not 0 <= p["x"] <= 105 or not 0 <= p["y"] <= 68 for p in points): errors.append(f"{snapshot['snapshot_id']} coordinate out of bounds")
        if not clips or any(c["render_mode"] != "svg_replay" or c["copyright"] != "original_synthetic" for c in clips): errors.append(f"{entry['match_id']} invalid clips")
        animation_by_id = {track["animation_track_id"]: track for track in animation["clips"]}
        for clip in clips:
            track = animation_by_id.get(clip["animation_track_id"])
            if not track or track["sample_rate_hz"] != 20 or not track["synthetic_presentation_only"]: errors.append(f"{clip['clip_id']} invalid animation track"); continue
            if {item["player_id"] for item in track["player_tracks"]} != starters: errors.append(f"{clip['clip_id']} animation must cover 22 starters")
            for player_track in track["player_tracks"]:
                if any(len(sample) != 7 or not 0 <= sample[1] <= 105 or not 0 <= sample[2] <= 68 for sample in player_track["samples"]): errors.append(f"{clip['clip_id']} invalid player animation sample")
            if any(len(sample) != 4 or not 0 <= sample[1] <= 105 or not 0 <= sample[2] <= 68 or sample[3] < 0 for sample in track["ball_track"]): errors.append(f"{clip['clip_id']} invalid ball animation sample")
        if tracking.get("source") != "authoritative_synthetic_simulation" or tracking.get("simulation_hz") != 20 or tracking.get("base_sample_rate_hz") != 4: errors.append(f"{entry['match_id']} invalid tracking manifest")
        if set(tracking.get("player_order", [])) != starters: errors.append(f"{entry['match_id']} tracking must cover 22 starters")
        last_time = -1
        grounded_frames = 0; controlled_frames = 0
        for chunk in tracking.get("chunks", []):
            chunk_path = folder / chunk["file"]
            if sha(chunk_path) != chunk["sha256"]: errors.append(f"{entry['match_id']} tracking chunk checksum mismatch")
            payload = load(chunk_path)
            for frame in payload.get("frames", []):
                if len(frame) != 7 or len(frame[5]) != 22 * 4 or frame[6] not in {"controlled", "flight", "dead"}: errors.append(f"{entry['match_id']} malformed tracking frame"); continue
                if frame[0] <= last_time: errors.append(f"{entry['match_id']} tracking frames not ordered")
                last_time = frame[0]
                if not 0 <= frame[1] <= 105 or not 0 <= frame[2] <= 68 or frame[3] < 0: errors.append(f"{entry['match_id']} invalid tracked ball")
                if frame[3] <= .05:
                    grounded_frames += 1
                    distances = [((frame[1] - frame[5][offset]) ** 2 + (frame[2] - frame[5][offset + 1]) ** 2) ** .5 for offset in range(0, len(frame[5]), 4)]
                    if min(distances, default=999) <= 1.5: controlled_frames += 1
                    if frame[6] == "controlled" and min(distances, default=999) > 1.5: errors.append(f"{entry['match_id']} controlled ball is not at a player's feet")
                for offset in range(0, len(frame[5]), 4):
                    x, y, vx, vy = frame[5][offset:offset + 4]
                    if not 0 <= x <= 105 or not 0 <= y <= 68: errors.append(f"{entry['match_id']} tracked player out of bounds")
                    if (vx * vx + vy * vy) ** .5 > 10: errors.append(f"{entry['match_id']} tracked player exceeds speed limit")
        if last_time != tracking.get("duration_ms"): errors.append(f"{entry['match_id']} tracking does not cover full match")
        if not grounded_frames or controlled_frames / grounded_frames < .85: errors.append(f"{entry['match_id']} ball control continuity below 85%")
    missing = required - all_kinds
    if missing: errors.append(f"missing scenario event types: {sorted(missing)}")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path, nargs="?", default=ROOT / "data" / "datasets" / "v1.3.0"); args = parser.parse_args()
    failures = validate(args.root)
    if failures: raise SystemExit("\n".join(failures))
    print(f"valid dataset: {args.root}")
