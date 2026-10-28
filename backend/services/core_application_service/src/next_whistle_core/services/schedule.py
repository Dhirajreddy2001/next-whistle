"""A match schedule that everyone sees the same way, worked out from the UTC clock.

Time is cut into slots, one match long. The slot that contains "now" is live (its window opens after half time, so there are events to see), the few before it are replays of the playable
matches, and the ones before those are results taken from the history file. Which match sits in which slot comes from a
seeded shuffle, so it never depends on who asks or when the server started.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

SEED = "next-whistle-schedule-v1"
PALETTE = (("#f6c453", "#33260a"), ("#ff9db1", "#3a1020"), ("#9be3c3", "#0f2e22"), ("#b9a7ff", "#221a4d"), ("#ffb07a", "#3a1c08"), ("#7dd3fc", "#0b2a3a"))


def _rank(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256(":".join([SEED, *map(str, parts)]).encode()).digest()[:8], "big")


HALF_TIME_MS = 45 * 60_000


def window_offset_ms(slot: int) -> int:
    """Where in the match a slot's live window starts: always after half time, 46' to 66', the same for everyone."""
    return HALF_TIME_MS + 60_000 + _rank("offset", slot) % (20 * 60_000)


def slot_of(now_ms: int, epoch_ms: int, duration_ms: int) -> int:
    return max(0, (now_ms - epoch_ms) // duration_ms)


def playable_for_slot(slot: int, match_ids: list[str]) -> str:
    """Each run of len(match_ids) slots plays every playable match once, in a seeded order."""
    ids = sorted(match_ids)
    order = sorted(ids, key=lambda match_id: _rank("play", slot // len(ids), match_id))
    return order[slot % len(ids)]


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _team(name: str, known: dict[str, dict]) -> dict:
    if name in known:
        return known[name]
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    primary, secondary = PALETTE[_rank("colour", name) % len(PALETTE)]
    return {"team_id": f"t_{slug}", "name": name, "short_name": re.sub(r"[^A-Za-z]", "", name)[:3].upper(), "colors": [primary, secondary]}


def build_schedule(now_ms: int, *, epoch_ms: int, duration_ms: int, matches: list[dict], history: list[dict], replays: int = 5, results: int = 30) -> dict:
    """Items are newest first: the live slot, then replays, then results, each with its start time in UTC."""
    known = {side["name"]: side for match in matches for side in (match["home"], match["away"])}
    by_id = {match["match_id"]: match for match in matches}
    slot = slot_of(now_ms, epoch_ms, duration_ms)
    past_history = sorted(history, key=lambda row: _rank("history", row["history_match_id"]))

    def start(of_slot: int) -> dict:
        starts_at_ms = epoch_ms + of_slot * duration_ms
        return {"slot": of_slot, "starts_at_ms": starts_at_ms, "starts_at": iso_utc(starts_at_ms), "duration_ms": duration_ms}

    live_id = playable_for_slot(slot, list(by_id))
    items = [{**start(slot), "kind": "live", "match_id": live_id, "home": by_id[live_id]["home"], "away": by_id[live_id]["away"], "live_match_ms": window_offset_ms(slot) + now_ms - (epoch_ms + slot * duration_ms)}]
    for earlier in range(slot - 1, max(-1, slot - replays - 1), -1):
        match_id = playable_for_slot(earlier, list(by_id))
        items.append({**start(earlier), "kind": "replay", "match_id": match_id, "home": by_id[match_id]["home"], "away": by_id[match_id]["away"]})
    first_result = slot - replays - 1
    for earlier in range(first_result, max(-1, first_result - results), -1):
        row = past_history[earlier % len(past_history)]
        moments = sorted(row["key_moments"], key=lambda moment: -moment["importance"])[:3]
        items.append({
            **start(earlier), "kind": "result", "home": _team(row["home"], known), "away": _team(row["away"], known),
            "score": {"home": row["score"]["home"], "away": row["score"]["away"]},
            "key_moments": sorted(({"minute": moment["minute"], "kind": moment["kind"], "team": moment["team"]} for moment in moments), key=lambda moment: moment["minute"]),
        })
    return {"generated_at_ms": now_ms, "items": items}
