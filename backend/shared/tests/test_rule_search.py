import json
from pathlib import Path

import pytest

from next_whistle_shared.evidence.rule_search import RuleIndex

RULES = json.loads((Path(__file__).resolve().parents[3] / "data/rules/v1.2.0/rules.json").read_text(encoding="utf-8"))
INDEX = RuleIndex(RULES)

GOLDEN = [
    ("what is a second yellow card", "r_law12_second_caution"),
    ("goalkeeper holding the ball too long", "r_law12_goalkeeper_eight_seconds"),
    ("penalty retake because the keeper moved", "r_law14_retake"),
    ("when is a goal disallowed for handball", "r_law12_handball_goal"),
    ("is a foul on the penalty area line a penalty", "r_law12_lines_inside_area"),
    ("when does the referee play advantage", "r_law12_advantage_cards"),
    ("does simulation get a card", "r_law12_simulation"),
    ("what does VAR check", "r_law5_var"),
    ("how big is the goal", "r_law1_goals"),
    ("how many players must a team have", "r_law3_minimum_players"),
    ("can the team replace a player who was sent off", "r_law3_sent_off_replacement"),
    ("goal kick rules", "r_law16_procedure"),
]


@pytest.mark.parametrize(("query", "expected"), GOLDEN)
def test_golden_questions_retrieve_the_expected_section_in_top_three(query, expected):
    assert expected in [rule["rule_id"] for _, rule in INDEX.search(query, 3)]


def test_search_is_deterministic_and_unrelated_queries_return_nothing():
    assert INDEX.search("second yellow card", 5) == INDEX.search("second yellow card", 5)
    assert INDEX.search("quantum chromodynamics", 3) == []
