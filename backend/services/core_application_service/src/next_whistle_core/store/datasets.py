"""Read-only repository for versioned, generated match datasets."""

from __future__ import annotations

import json
import hashlib
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MatchData:
    match: dict
    events: tuple[dict, ...]
    snapshots: tuple[dict, ...]
    tracking_manifest: dict
    folder: Path

    @property
    def duration_ms(self) -> int:
        return max(period["end_match_ms"] for period in self.match["periods"])


@dataclass(frozen=True)
class Dataset:
    version: str
    catalog: dict
    matches: dict[str, MatchData]
    rules_version: str = "1.2.0"


def _read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=128)
def _read_tracking_chunk(path: str) -> dict:
    return _read_json(Path(path))


def tracking_frames(data: MatchData, cutoff_ms: int):
    """Yield only stored frames that have occurred by the requested cutoff."""
    for chunk in data.tracking_manifest.get("chunks", []):
        if chunk["from_ms"] > cutoff_ms:
            break
        path = data.folder / chunk["file"]
        for frame in _read_tracking_chunk(str(path)).get("frames", []):
            if frame[0] > cutoff_ms:
                break
            yield frame


def load_dataset(root: Path) -> Dataset:
    manifest = _read_json(root / "manifest.json")
    catalog_path = root / "catalog.json"
    catalog = _read_json(catalog_path) if catalog_path.is_file() else None

    loaded: dict[str, MatchData] = {}
    for entry in manifest["matches"]:
        match_id = entry["match_id"]
        folder = (root / match_id).resolve()
        if root.resolve() not in folder.parents:
            raise ValueError(f"Invalid dataset path for {match_id}")
        for name, relative_path in entry["files"].items():
            path = (root / relative_path).resolve()
            if root.resolve() not in path.parents:
                raise ValueError(f"Invalid {name} path for {match_id}")
            expected = entry.get("sha256", {}).get(name)
            if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Dataset checksum mismatch for {match_id} {name}")
        match = _read_json(folder / "match.json")
        events = _read_json(folder / "events.json")
        snapshots = _read_json(folder / "snapshots.json")
        tracking_manifest = _read_json(folder / "tracking" / "manifest.json")
        if match.get("match_id") != match_id:
            raise ValueError(f"Dataset match ID mismatch for {match_id}")
        if any(event["available_ms"] < event["match_ms"] for event in events):
            raise ValueError(f"Event available before occurrence in {match_id}")
        loaded[match_id] = MatchData(
            match=match,
            events=tuple(sorted(events, key=lambda event: (event["match_ms"], event["seq"]))),
            snapshots=tuple(sorted(snapshots, key=lambda snapshot: snapshot["match_ms"])),
            tracking_manifest=tracking_manifest,
            folder=folder,
        )

    if catalog is None:
        catalog_matches = []
        for entry in manifest["matches"]:
            match = loaded[entry["match_id"]].match
            row = {"match_id": entry["match_id"], "label": entry["label"], "kickoff_date": entry["kickoff_date"]}
            for side_name in ("home", "away"):
                side = match[side_name]
                row[side_name] = {"team_id": side["team_id"], "name": side["name"], "short_name": side["short_name"], "colors": [side["primary_color"], side["secondary_color"]]}
            catalog_matches.append(row)
        catalog = {"dataset_version": manifest["dataset_version"], "matches": catalog_matches}
    if catalog.get("dataset_version") != manifest.get("dataset_version"):
        raise ValueError("Catalog and dataset versions differ")

    if {entry["match_id"] for entry in catalog["matches"]} != set(loaded):
        raise ValueError("Catalog and dataset match IDs differ")
    return Dataset(manifest["dataset_version"], catalog, loaded, manifest.get("rules_version", "1.2.0"))
