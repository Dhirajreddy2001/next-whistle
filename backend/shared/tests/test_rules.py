import json
from pathlib import Path
from typing import get_args

from next_whistle_shared.evidence.tool_schemas import INCIDENT_TAGS

RULES = json.loads((Path(__file__).resolve().parents[3] / "data/rules/v1.2.0/rules.json").read_text(encoding="utf-8"))


def test_rule_ids_are_unique_and_cite_ifab():
    ids = [rule["rule_id"] for rule in RULES]
    assert len(ids) == len(set(ids))
    for rule in RULES:
        assert rule["source_url"].startswith("https://www.theifab.com/laws/latest/")
        assert rule["edition"] == "2026/27"
        assert 1 <= len(rule["summary"]) <= 700


def test_every_incident_tag_is_declared_and_has_a_rule():
    tagged = {tag for rule in RULES for tag in rule["incident_tags"]}
    assert tagged == set(get_args(INCIDENT_TAGS))
