"""Read-only loader for the pinned v1.3.0 synthetic evidence dataset."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvidenceMatch:
    match: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    snapshots: tuple[dict[str, Any], ...]
    kickoff_date: str
    label: str


@dataclass(frozen=True)
class EvidenceDataset:
    version: str
    rules_version: str
    matches: dict[str, EvidenceMatch]
    rules: tuple[dict[str, Any], ...]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_evidence_dataset(dataset_path: Path, rules_path: Path | None = None) -> EvidenceDataset:
    """Load immutable match/rule files and verify every manifest checksum."""
    root = dataset_path.resolve()
    manifest = _read_json(root / "manifest.json")
    version = manifest.get("dataset_version")
    if version != "1.3.0":
        raise ValueError(f"Evidence tools require dataset v1.3.0, got {version!r}")
    rules_version = manifest["rules_version"]
    if rules_path is None:
        repository_root = root.parents[2]
        rules_path = repository_root / "data" / "rules" / f"v{rules_version}" / "rules.json"
    rules_file = rules_path.resolve()
    rules = tuple(_read_json(rules_file))

    matches: dict[str, EvidenceMatch] = {}
    for entry in manifest["matches"]:
        match_id = entry["match_id"]
        files = entry["files"]
        loaded: dict[str, Any] = {}
        for name in ("match", "events", "snapshots"):
            candidate = (root / files[name]).resolve()
            if root not in candidate.parents:
                raise ValueError(f"Dataset path escapes root: {name}")
            expected = entry.get("sha256", {}).get(name)
            if expected and hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Dataset checksum mismatch for {match_id}/{name}")
            loaded[name] = _read_json(candidate)
        if loaded["match"].get("match_id") != match_id:
            raise ValueError(f"Match ID mismatch for {match_id}")
        matches[match_id] = EvidenceMatch(
            match=loaded["match"],
            events=tuple(sorted(loaded["events"], key=lambda row: (row["match_ms"], row["seq"]))),
            snapshots=tuple(sorted(loaded["snapshots"], key=lambda row: (row["match_ms"], row["snapshot_id"]))),
            kickoff_date=entry["kickoff_date"],
            label=entry["label"],
        )
    return EvidenceDataset(version, rules_version, matches, rules)
