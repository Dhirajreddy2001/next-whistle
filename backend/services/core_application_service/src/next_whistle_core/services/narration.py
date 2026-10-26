"""Plain-language fallback text, filled from the recorded events, for when the agent cannot answer.

Everything here is a template: the wording is fixed, and only names, minutes, scores and rule text are filled in from the
dataset, in English or Spanish. Nothing is invented, so these sentences are always safe to show.
"""

from __future__ import annotations

from typing import Any

from next_whistle_core.store.datasets import MatchData

_SHOT_RESULT = {
    "en": {"on_target": "on target", "saved": "saved by the goalkeeper", "off_target": "off target", "post": "off the post"},
    "es": {"on_target": "a puerta", "saved": "parado por el portero", "off_target": "fuera", "post": "al poste"},
}
_REASONS = {
    "en": {"reckless": "a reckless challenge", "stopping_promising_attack": "stopping a promising attack", "second_yellow": "a second yellow card"},
    "es": {"reckless": "una entrada temeraria", "stopping_promising_attack": "cortar un ataque prometedor", "second_yellow": "una segunda amarilla"},
}
_ACTIONS = {
    "en": {"pass": "played a pass", "carry": "carried the ball forward", "interception": "intercepted the ball", "tackle": "won the ball with a tackle",
           "clearance": "cleared the ball", "pressure": "pressed the player on the ball", "save": "made a save", "foul": "committed a foul"},
    "es": {"pass": "dio un pase", "carry": "condujo el balón hacia delante", "interception": "interceptó el balón", "tackle": "recuperó el balón con una entrada",
           "clearance": "despejó el balón", "pressure": "presionó al jugador con balón", "save": "hizo una parada", "foul": "cometió una falta"},
}
_RULINGS = {  # what the recorded decision means for the viewer, per decision type (paraphrased, not the Laws' wording)
    "es": {
        "offside": "Un jugador está en fuera de juego si, cuando se juega el balón hacia él, está más cerca de la línea de meta rival que el balón y que el penúltimo adversario.",
        "penalty": "Se concede penalti cuando un jugador comete, dentro de su propia área, una falta que se castiga con tiro libre directo.",
        "tactical_foul_yellow": "Una falta táctica que corta un ataque prometedor se castiga con tarjeta amarilla.",
    },
}
_FIXED = {
    "en": {"kickoff": "Kick-off.", "period_end": "The period has ended.", "period_start": "The second half is under way."},
    "es": {"kickoff": "Saque inicial.", "period_end": "Ha terminado el periodo.", "period_start": "Ha comenzado la segunda parte."},
}


def _lang(language: str) -> str:
    return "es" if language.startswith("es") else "en"


class Names:
    def __init__(self, data: MatchData) -> None:
        self.teams = {side["team_id"]: side["name"] for side in (data.match["home"], data.match["away"])}
        self.players = {p["player_id"]: p["name"] for side in (data.match["home"], data.match["away"]) for p in side["squad"]}
        self.home, self.away = data.match["home"]["team_id"], data.match["away"]["team_id"]

    def player(self, player_id: str | None) -> str:
        return self.players.get(player_id or "", "a player")

    def team(self, team_id: str | None) -> str:
        return self.teams.get(team_id or "", "a team")

    def other(self, team_id: str) -> str:
        return self.away if team_id == self.home else self.home


def score_at(data: MatchData, cutoff_ms: int) -> dict[str, int]:
    names = Names(data)
    visible = [e for e in data.events if e["available_ms"] <= cutoff_ms]
    reversed_ids = {e["decision"].get("reverses_event_id") for e in visible if e["type"] == "goal_disallowed" and e.get("decision")}
    score = {names.home: 0, names.away: 0}
    for goal in visible:
        if goal["type"] == "goal" and goal["event_id"] not in reversed_ids:
            score[goal["team_id"]] += 1
    return score


def scoreline(data: MatchData, cutoff_ms: int, language: str) -> str:
    names, score = Names(data), score_at(data, cutoff_ms)
    return f"{names.teams[names.home]} {score[names.home]}–{score[names.away]} {names.teams[names.away]}"


def _minute(event: dict) -> int:
    return event["match_ms"] // 60_000 + 1


def _related(data: MatchData, event: dict, kinds: set[str]) -> dict | None:
    by_id = {e["event_id"]: e for e in data.events}
    return next((by_id[i] for i in event.get("related_event_ids", []) if i in by_id and by_id[i]["type"] in kinds), None)


def describe_event(data: MatchData, event: dict, language: str, cutoff_ms: int | None = None) -> str:
    """One sentence about one recorded event."""
    lang, names = _lang(language), Names(data)
    who, team, minute = names.player(event.get("player_id")), names.team(event.get("team_id")), _minute(event)
    kind, qualifiers = event["type"], event.get("qualifiers", [])
    es = lang == "es"
    if kind == "goal":
        assist = _related(data, event, {"pass"}) or _related(data, _related(data, event, {"shot"}) or event, {"pass"})
        helper = names.player(assist["player_id"]) if assist and assist.get("player_id") != event.get("player_id") else None
        score = scoreline(data, cutoff_ms, language) if cutoff_ms is not None else None
        if es:
            return f"Min. {minute}: {who} marcó para {team}" + (f", tras un pase de {helper}" if helper else "") + "." + (f" Marcador: {score}." if score else "")
        return f"{minute}': {who} scored for {team}" + (f", set up by {helper}" if helper else "") + "." + (f" It is {score}." if score else "")
    if kind == "goal_disallowed":
        scorer = names.player(event.get("player_id"))
        return (f"Min. {minute}: el gol de {scorer} ({team}) se anuló tras la revisión por fuera de juego." if es
                else f"{minute}': the goal by {scorer} ({team}) was ruled out after a review for offside.")
    if kind == "offside":
        return (f"Min. {minute}: se señaló fuera de juego a {who} ({team}) y se detuvo la jugada." if es
                else f"{minute}': {who} ({team}) was flagged offside and the attack was stopped.")
    if kind in {"card_yellow", "card_red"}:
        colour = ("amarilla" if es else "yellow") if kind == "card_yellow" else ("roja" if es else "red")
        reason = next((_REASONS[lang][q] for q in qualifiers if q in _REASONS[lang]), None)
        if es:
            return f"Min. {minute}: {who} ({team}) vio la tarjeta {colour}" + (f" por {reason}" if reason else "") + "."
        return f"{minute}': {who} ({team}) was shown a {colour} card" + (f" for {reason}" if reason else "") + "."
    if kind == "shot":
        if event.get("outcome") == "goal":
            return f"Min. {minute}: {who} ({team}) marcó." if es else f"{minute}': {who} ({team}) scored."
        result = _SHOT_RESULT[lang].get(event.get("outcome") or "")
        if es:
            return f"Min. {minute}: {who} ({team}) disparó" + (f", {result}." if result else ".")
        return f"{minute}': {who} ({team}) took a shot" + (f", {result}." if result else ".")
    if kind == "save":
        return f"Min. {minute}: {who} ({team}) hizo una parada." if es else f"{minute}': {who} ({team}) made a save."
    if kind == "substitution":
        coming = names.player(event.get("target_player_id"))
        return (f"Min. {minute}: {team} cambió a {who} por {coming}." if es else f"{minute}': {team} replaced {who} with {coming}.")
    if kind == "foul" and "penalty" in qualifiers:
        fouled = names.player(event.get("target_player_id"))
        return (f"Min. {minute}: {who} ({team}) cometió falta sobre {fouled} dentro del área y se señaló penalti." if es
                else f"{minute}': {who} ({team}) fouled {fouled} inside the area and a penalty was awarded.")
    if kind in _FIXED[lang]:
        return _FIXED[lang][kind]
    action = _ACTIONS[lang].get(kind, "tomó parte en la jugada" if es else "was involved in the play")
    return f"Min. {minute}: {who} ({team}) {action}." if es else f"{minute}': {who} ({team}) {action}."


def explain_fallback(data: MatchData, cutoff_ms: int, language: str, event: dict | None) -> str:
    """The fallback for an Explain this moment or a recap request."""
    lang = _lang(language)
    if event is not None:
        return describe_event(data, event, language, cutoff_ms)
    key = {"goal", "goal_disallowed", "card_red", "card_yellow", "save", "substitution", "offside"}
    recent = [e for e in data.events if e["available_ms"] <= cutoff_ms and e["type"] in key][-3:]
    score = scoreline(data, cutoff_ms, language)
    minute = cutoff_ms // 60_000 + 1
    lead = f"Min. {minute}: {score}." if lang == "es" else f"At {minute}' it is {score}."
    if not recent:
        return lead + (" Todavía no ha ocurrido nada destacado." if lang == "es" else " Nothing major has happened yet.")
    heading = " Lo último:" if lang == "es" else " Latest:"
    return lead + heading + " " + " ".join(describe_event(data, e, language) for e in recent)


def referee_fallback_text(data: MatchData, event: dict, language: str, rule: dict[str, Any] | None, level: str) -> str:
    """The recorded decision in plain words, followed by the principle behind it."""
    names, es = Names(data), _lang(language) == "es"
    decision = event["decision"]
    kind, who, team, minute = decision["decision_type"], names.player(event.get("player_id")), names.team(event.get("team_id")), _minute(event)
    victim = event.get("target_player_id") or (_related(data, event, {"foul"}) or {}).get("target_player_id")
    target = names.players.get(victim or "", "un rival" if es else "an opponent")
    if kind == "offside":
        if event["type"] == "goal_disallowed":
            ruling = (f"El gol de {who} ({team}) en el minuto {minute} se anuló: tras la revisión, estaba en fuera de juego." if es
                      else f"The goal by {who} ({team}) at {minute}' was ruled out: after a review, {who} was offside.")
        else:
            ruling = (f"En el minuto {minute} se señaló fuera de juego a {who} ({team}) y se detuvo el ataque." if es
                      else f"At {minute}' {who} ({team}) was flagged offside and the attack was stopped.")
    elif kind == "penalty":
        ruling = (f"En el minuto {minute}, {who} ({team}) cometió falta sobre {target} dentro del área y se concedió penalti." if es
                  else f"At {minute}', {who} ({team}) fouled {target} inside the area, so a penalty was awarded.")
    elif kind == "tactical_foul_yellow":
        ruling = (f"En el minuto {minute}, {who} ({team}) derribó a {target} para cortar un ataque prometedor y vio la tarjeta amarilla." if es
                  else f"At {minute}', {who} ({team}) brought down {target} to stop a promising attack and was shown a yellow card.")
    else:
        ruling = describe_event(data, event, language)
    if es:
        principle = _RULINGS["es"].get(kind)
    else:
        principle = (rule.get("plain") if rule and level == "beginner" and rule.get("plain") else rule["summary"]) if rule else None
    return f"{ruling} {principle}" if principle else ruling
