"""Viewer expertise profiles. Only the Storyteller sees these; facts never change with level."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LevelProfile:
    name: str
    max_numbers: int
    max_words: int
    instructions: str


LEVEL_PROFILES: dict[str, LevelProfile] = {
    "beginner": LevelProfile(
        "beginner", 2, 120,
        "The viewer is new to football. Use plain everyday words and no jargon. If you must use a football term "
        "(for example offside or caution), explain it in a few words. Use short sentences. Include at most two numbers.",
    ),
    "intermediate": LevelProfile(
        "intermediate", 4, 180,
        "The viewer follows football regularly. Common terms such as press, shot on target or possession are fine. "
        "Add one piece of context beyond what happened. Include at most four numbers.",
    ),
    "pro": LevelProfile(
        "pro", 6, 250,
        "The viewer is an expert. Use precise tactical and officiating vocabulary, mention measured margins and the "
        "exact Law involved, and be concise. Include at most six numbers.",
    ),
}


def profile_for(level: str) -> LevelProfile:
    return LEVEL_PROFILES.get(level, LEVEL_PROFILES["beginner"])
