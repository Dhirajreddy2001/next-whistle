"""Deterministic stand-in for the model roles.

It reads only what a real role may read: tool results through the bound dispatcher for the Analyst and
Referee, and the validated brief for the Storyteller. No model is called, so it costs nothing.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from next_whistle_shared.contracts.agent import (
    BriefStatistic, Claim, CriticVerdict, DataFinding, DecisionRuling, EvidenceBrief, EvidenceFact, EvidenceFactor,
    Explanation, FocusPlayer, LawSummary, RefereeAnswer, RuleExplanation, Significance,
)
from next_whistle_shared.evidence.dispatcher import ToolDispatcher

from .runner import RunContext, Usage

SUPPORTED_DECISIONS = {"offside", "tactical_foul_yellow", "penalty"}

_ES_LABELS = {
    "goal": "gol", "goal_disallowed": "gol anulado", "card_yellow": "tarjeta amarilla", "card_red": "tarjeta roja",
    "foul": "falta", "shot": "disparo", "save": "parada", "substitution": "cambio", "offside": "fuera de juego",
    "pass": "pase", "carry": "conducción", "tackle": "entrada", "interception": "intercepción", "clearance": "despeje",
    "kickoff": "saque inicial", "period_end": "final del periodo", "period_start": "inicio del periodo",
    "pressure": "presión", "recovery": "recuperación",
}


class RunnerError(Exception):
    """A role could not produce output (for example a required tool failed)."""


class InvalidOutput(RunnerError):
    """The model answered, but not in the required shape. The orchestrator treats it as a validation failure and retries once."""


def _label(event_type: str, language: str) -> str:
    if language.startswith("es"):
        return _ES_LABELS.get(event_type, event_type.replace("_", " "))
    return event_type.replace("_", " ")


def _first_sentences(text: str, limit: int = 400) -> str:
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit]


class ScriptedRunner:
    source = "fake"

    def __init__(
        self,
        corrupt_brief: Callable[[EvidenceBrief, RunContext], EvidenceBrief] | None = None,
        corrupt_answer: Callable[[BaseModel, RunContext], BaseModel] | None = None,
        critic_verdicts: list[str] | None = None,
    ):
        self.usage = Usage()
        self.corrupt_brief, self.corrupt_answer = corrupt_brief, corrupt_answer
        self.critic_verdicts = list(critic_verdicts or ["pass"])
        self.calls: list[str] = []

    async def run_tool_agent(self, *, role, instructions, user_input, dispatcher, output_type, run):
        self.usage.add()
        self.calls.append(role)
        brief = self._brief(role, dispatcher, run)
        if self.corrupt_brief is not None:
            brief = self.corrupt_brief(brief, run)
        return brief

    async def run_writer(self, *, role, instructions, user_input, output_type, run):
        self.usage.add()
        self.calls.append(role)
        if role == "critic":
            verdict = self.critic_verdicts.pop(0) if self.critic_verdicts else "pass"
            return CriticVerdict(verdict=verdict)
        assert run.brief is not None
        answer = self._referee_answer(run) if run.context.intent in {"decision", "referee_question"} else self._explanation(run)
        if self.corrupt_answer is not None:
            answer = self.corrupt_answer(answer, run)
        return answer

    # ---- Analyst / Referee -------------------------------------------------------------------------------

    def _brief(self, role: str, dispatcher: ToolDispatcher, run: RunContext) -> EvidenceBrief:
        context = run.context
        match = dispatcher.match.match
        players = {p["player_id"]: p for side in (match["home"], match["away"]) for p in side["squad"]}
        teams = {match["home"]["team_id"]: match["home"]["name"], match["away"]["team_id"]: match["away"]["name"]}
        state = dispatcher.call("get_match_state", {})
        if not state.ok:
            raise RunnerError("Match state is unavailable.")
        event_id = context.selected_event_id or state.data["lastEventId"]
        if not event_id:
            raise RunnerError("No cutoff-visible event is available.")
        evidence = dispatcher.call("get_event_evidence", {"event_id": event_id, "include_build_up": True, "include_snapshots": True})
        if not evidence.ok:
            raise RunnerError(f"Event evidence unavailable: {evidence.error.code}")
        event = evidence.data["event"]
        minute = f"{event['matchMs'] // 60_000 + 1}'"
        who = players.get(event.get("playerId"), {}).get("name")
        team = teams.get(event.get("teamId"), "")
        subject = f"{who} ({team})" if who else team or "A player"
        facts = [EvidenceFact(id="f1", text=f"{subject}: {event['type'].replace('_', ' ')} at {minute}.", evidence_ids=[event_id])]
        involvement = {pid: [event_id] for pid in {event.get("playerId"), event.get("targetPlayerId")} if pid}
        decision = event.get("decision")
        if decision:
            facts.append(EvidenceFact(id=f"f{len(facts) + 1}", text=f"The recorded ruling was {decision['recorded_ruling'].replace('_', ' ')}.", evidence_ids=[event_id]))
        build_up = evidence.data.get("buildUp") or []
        if len(build_up) >= 2:
            facts.append(EvidenceFact(id=f"f{len(facts) + 1}", text="The move was built up through several earlier actions.", evidence_ids=[build_up[0]["eventId"], build_up[-1]["eventId"]]))

        stats = []
        stat_result = dispatcher.call("get_match_statistics", {"metrics": ["shots", "goals"], "scope": "team"}) if role == "analyst" else None
        if stat_result is not None and stat_result.ok:
            for row in stat_result.data["statistics"]:
                stats.append(BriefStatistic(stat_ref=row["statRef"], value=row["value"], display=f"{teams.get(row['teamId'], row['teamId'])} {row['metric']}"))

        score = state.data["score"]
        if event["type"] == "goal":
            significance = Significance(established=True, text=f"After this goal the score is {score['home']}–{score['away']}.", fact_ids=["f1"])
        else:
            significance = Significance(established=False, text="", fact_ids=[])
        ruling = None
        if role == "referee" and decision and decision["decision_type"] in SUPPORTED_DECISIONS:
            geometry = dispatcher.call("get_decision_geometry", {"event_id": event_id})
            rules = dispatcher.call("get_rule_reference", {"incident_tag": decision["kind"] if decision["kind"] in {"offside", "penalty"} else "tactical_foul_yellow"})
            if geometry.ok and rules.ok:
                rule_rows = rules.data["rules"]
                if decision["decision_type"] == "offside":
                    margin = geometry.data.get("marginM")
                    finding = "not_settled" if geometry.data.get("withinUncertainty") or margin is None else ("supports" if margin > 0 else "contradicts")
                else:
                    finding = "supports"  # no positional test applies; the recorded event is the evidence
                ruling = DecisionRuling(
                    decision_event_id=event_id, decision_type=decision["decision_type"], recorded_ruling=decision["recorded_ruling"],
                    law_ids=[row["ruleId"] for row in rule_rows[:4]], requirement=_first_sentences(rule_rows[0]["summary"]),
                    data_finding=finding, geometry=geometry.data,
                    finding_reason=("The measured margin is too small to settle the call." if finding == "not_settled" else "The recorded event is compared with the Law's requirement."),
                )
        return EvidenceBrief(
            brief_id="b_" + hashlib.sha256(f"{context.request_id}{event_id}{role}".encode()).hexdigest()[:16],
            intent=context.intent, event_id=context.selected_event_id, facts=facts, stats=stats,
            factors=[EvidenceFactor(id="k1", text=f"The event log records {event['type'].replace('_', ' ')}.", fact_ids=["f1"], strength="supported")],
            significance=significance,
            uncertainty=[] if significance.established else ["The record does not show how much this moment changed the match."],
            involvement=involvement, ruling=ruling,
        )

    # ---- Storyteller -------------------------------------------------------------------------------------

    def _explanation(self, run: RunContext) -> Explanation:
        brief, context = run.brief, run.context
        es = context.language.startswith("es")
        level = context.level
        fact_texts = [fact.text for fact in brief.facts]
        stats = {s.stat_ref: s for s in brief.stats}
        claims: list[Claim] = [Claim(id="c1", kind="fact", text=fact_texts[0], evidence_ids=brief.facts[0].evidence_ids)]
        number_refs: list[BriefStatistic] = []
        if level == "intermediate":
            number_refs = [s for s in brief.stats if ".shots." in s.stat_ref][:2]
        elif level == "pro":
            number_refs = [s for s in brief.stats if ".shots." in s.stat_ref][:2] + [s for s in brief.stats if ".goals." in s.stat_ref][:2]
        for index, stat in enumerate(number_refs, 2):
            claims.append(Claim(id=f"c{index}", kind="number", text=f"{stat.display}: {stat.value}", stat_ref=stat.stat_ref, value=stat.value, evidence_ids=brief.facts[0].evidence_ids))
        what = " ".join(fact_texts[:2] if level != "beginner" else fact_texts[:1])
        if number_refs:
            what += " " + "; ".join(f"{stat.display} {stat.value}" for stat in number_refs) + "."
        matters = brief.significance.text or ("The record does not show how much this moment changed the match." if not es else "El registro no muestra cuánto cambió este momento el partido.")
        if level == "beginner" and not brief.significance.text:
            matters = "It is one more moment in the match." if not es else "Es un momento más del partido."
        if es:
            what = "Registro del partido: " + what
        headline = (fact_texts[0].split(": ", 1)[-1].rstrip(".").capitalize()[:80]) or "Match moment"
        focus = None
        if context.focus_player_id:
            involved = context.focus_player_id in brief.involvement
            focus = FocusPlayer(player_id=context.focus_player_id, involved=involved, event_ids=brief.involvement.get(context.focus_player_id, []),
                                text="" if involved else "This player was not involved in this moment.")
        return Explanation(
            headline=headline, what_happened=what[:600], factors=list(brief.factors)[:6], why_it_matters=matters[:400],
            significance_established=brief.significance.established, uncertainty=(brief.uncertainty[0] if brief.uncertainty else "")[:240],
            focus_player=focus, claims=claims,
        )

    def _referee_answer(self, run: RunContext) -> RefereeAnswer:
        brief, context = run.brief, run.context
        ruling = brief.ruling
        if ruling is None:
            raise RunnerError("The brief has no ruling to explain.")
        level, es = context.level, context.language.startswith("es")
        margin = (ruling.geometry or {}).get("marginM")
        geometry_ref = (ruling.geometry or {}).get("geometryRef")
        claims = [Claim(id="c1", kind="fact", text=f"Recorded ruling: {ruling.recorded_ruling}.", evidence_ids=[ruling.decision_event_id])]
        finding_text = {
            "supports": "The recorded positions back up the call.",
            "contradicts": "The recorded positions do not back up the call.",
            "not_settled": "The positions are too close to settle the call from the recorded data.",
        }[ruling.data_finding]
        if level != "beginner" and ruling.decision_type == "offside" and margin is not None and geometry_ref:
            claims.append(Claim(id="c2", kind="number", text=f"Margin beyond the line: {margin}", geometry_ref=geometry_ref, value=margin, evidence_ids=[ruling.decision_event_id]))
            finding_text += f" The attacker's margin beyond the line was {margin} metres."
        law_text = ruling.requirement
        if level == "beginner":
            law_text = _first_sentences(law_text, 200)
        if es:
            finding_text = "Los datos registrados " + {"supports": "respaldan la decisión.", "contradicts": "no respaldan la decisión.", "not_settled": "no bastan para resolverla."}[ruling.data_finding]
        return RefereeAnswer(
            headline=f"Recorded ruling: {ruling.recorded_ruling.replace('_', ' ')}"[:90], decision=ruling.recorded_ruling,
            law=LawSummary(law_ids=ruling.law_ids, text=law_text[:400]), data_shows=DataFinding(finding=ruling.data_finding, text=finding_text[:400]),
            uncertainty=("The geometry comes from one recorded snapshot." if level != "beginner" else "")[:240], claims=claims,
        )
