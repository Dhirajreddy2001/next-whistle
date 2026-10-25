"""Routed workflow: Analyst or Referee -> Storyteller -> optional Critic, with code validation between steps.

Every failure route is taken at most once per request. A second failure returns the factual fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from next_whistle_shared.contracts.agent import (
    AgentRequest, AgentResponse, BoundContext, CriticVerdict, EvidenceBrief, Explanation, RefereeAnswer,
)
from next_whistle_shared.evidence.dataset import EvidenceDataset
from next_whistle_shared.evidence.dispatcher import EvidenceLedger, ToolDispatcher
from next_whistle_shared.evidence.replay import ledger_from_brief
from next_whistle_shared.validation.evidence_validator import EvidenceValidator, ValidationReport

from .prompts import PROMPT_VERSION, critic_input, role_prompt, storyteller_input, storyteller_instructions, tool_agent_input
from .runner import AgentRunner, RunContext
from .scripted import InvalidOutput, RunnerError

WORKFLOW_VERSION = f"agents-v1-p{PROMPT_VERSION}"
EVIDENCE_FAILURES = {"V2", "V3", "V8", "V9", "V15"}  # the evidence itself is missing: the tool-capable agent must retry


@dataclass
class WorkflowTrace:
    steps: list[dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    fallback_reason: str | None = None

    def add(self, step: str, **detail: Any) -> None:
        self.steps.append({"step": step, **detail})


@dataclass
class WorkflowResult:
    response: AgentResponse
    trace: WorkflowTrace
    ledger: EvidenceLedger | None = None


def _involvement(brief: EvidenceBrief, ledger: EvidenceLedger) -> dict[str, list[str]]:
    """Who acted in the cited events, worked out from the data: a model's own mapping is often malformed."""
    involved: dict[str, list[str]] = {}
    for event_id in sorted({e for fact in brief.facts for e in fact.evidence_ids if e.startswith("e_")}):
        event = ledger.visible_events.get(event_id)
        for player_id in {event.get("player_id"), event.get("target_player_id")} - {None} if event else set():
            involved.setdefault(player_id, []).append(event_id)
    return involved


def _normalise(brief: EvidenceBrief, ledger: EvidenceLedger) -> EvidenceBrief:
    """Deterministic tidy-up of fields a model gets wrong without it changing any fact.

    The Core has already decided a request reaching the agent is in scope, so the label is forced rather than guessed;
    and a moment whose wider impact is not established must say so, which the model sometimes forgets.
    """
    update: dict = {"scope": "in_scope", "redirect_event_id": None, "involvement": _involvement(brief, ledger)}
    if not brief.significance.established and not brief.significance.text and not brief.uncertainty:
        update["uncertainty"] = ["The record does not show how much this moment changed the match."]
    return brief.model_copy(update=update)


def _normalise_answer(answer, brief: EvidenceBrief, ledger: EvidenceLedger):
    """The recorded ruling and the cited Laws are data, so a referee answer carries them exactly as recorded."""
    if isinstance(answer, Explanation) and answer.rule is not None and answer.rule.rule_id not in ledger.rules:
        return answer.model_copy(update={"rule": None})  # only a rule a tool actually returned may be cited
    if isinstance(answer, RefereeAnswer) and brief.ruling is not None:
        allowed = [law for law in answer.law.law_ids if law in brief.ruling.law_ids] or list(brief.ruling.law_ids)
        return answer.model_copy(update={"decision": brief.ruling.recorded_ruling, "law": answer.law.model_copy(update={"law_ids": allowed[:4]})})
    return answer


def _failures(report: ValidationReport) -> list[str]:
    return [f"{issue.check_id}: {issue.message}" for issue in report.issues if issue.severity == "error"]


def _squads_with_teams(dispatcher: ToolDispatcher) -> dict[str, dict]:
    match = dispatcher.match.match
    people = {p["player_id"]: p for side in (match["home"], match["away"]) for p in side["squad"]}
    people.update({side["team_id"]: {"name": side["name"]} for side in (match["home"], match["away"])})
    return people


def _answer_text(answer: Explanation | RefereeAnswer) -> str:
    headline = answer.headline.rstrip(" .")
    if isinstance(answer, RefereeAnswer):
        return f"{headline}. {answer.law.text} {answer.data_shows.text}".strip()
    return f"{headline}. {answer.what_happened} {answer.why_it_matters}".strip()


def _fallback(payload: AgentRequest, trace: WorkflowTrace, reason: str, brief: EvidenceBrief | None = None) -> WorkflowResult:
    trace.fallback_reason = reason
    facts = " ".join(fact.text for fact in brief.facts) if brief else ""
    evidence = sorted({e for fact in brief.facts for e in fact.evidence_ids}) if brief else []
    answer = facts or "The recorded match facts are available, but a written explanation could not be produced just now."
    response = AgentResponse(
        request_id=payload.request_id, status="fallback", source="template", workflow_version=WORKFLOW_VERSION,
        answer=answer[:2400], evidence_ids=evidence[:24],
    )
    return WorkflowResult(response, trace)


async def run_workflow(
    payload: AgentRequest, dataset: EvidenceDataset, runner: AgentRunner, *, critic_enabled: bool = False,
) -> WorkflowResult:
    context = payload.context
    assert context is not None
    trace = WorkflowTrace()
    tool_role = "referee" if context.intent in {"decision", "referee_question"} else "analyst"
    ledger = EvidenceLedger()
    # A system dispatcher shares the ledger so the verified score is available to the validator
    # without spending the agent's own tool-call budget.
    state = ToolDispatcher(context.for_agent(tool_role), dataset, ledger).call("get_match_state", {})
    if state.ok:
        ledger.visible_events["__match_state__"] = {**state.data, "available_ms": context.cutoff_ms, "match_ms": state.data["clockMs"]}
    agent_dispatcher = ToolDispatcher(context.for_agent(tool_role), dataset, ledger)
    validator = EvidenceValidator(context, ledger, _squads_with_teams(agent_dispatcher))

    run = RunContext(context=context, payload=payload)

    # A brief the Core cached earlier is level-free, so it can be reused for any viewer level or language.
    # It is checked against the dataset again at the time it was computed; if anything is off it is ignored.
    brief: EvidenceBrief | None = None
    from_cache = False
    brief_cutoff = context.cutoff_ms
    if payload.cached_brief is not None:
        cache_context = BoundContext.model_validate({**context.model_dump(), "cutoff_ms": payload.brief_cutoff_ms, "evidence_cutoff_ms": payload.brief_cutoff_ms})
        cache_ledger = ledger_from_brief(payload.cached_brief, dataset, context.match_id, payload.brief_cutoff_ms)
        accepted = False
        if cache_ledger is not None:
            cache_state = ToolDispatcher(cache_context.for_agent(tool_role), dataset, cache_ledger).call("get_match_state", {})
            if cache_state.ok:
                cache_ledger.visible_events["__match_state__"] = {**cache_state.data, "available_ms": payload.brief_cutoff_ms, "match_ms": cache_state.data["clockMs"]}
                cache_validator = EvidenceValidator(cache_context, cache_ledger, _squads_with_teams(agent_dispatcher))
                accepted = cache_validator.validate(payload.cached_brief).passed
        trace.add("brief.cache", used=accepted)
        if accepted:
            brief, from_cache, brief_cutoff = payload.cached_brief, True, payload.brief_cutoff_ms
            ledger, validator = cache_ledger, cache_validator  # answers are checked against the same time the brief describes

    async def produce_brief(failures: list[str] | None) -> EvidenceBrief:
        run.failures = failures or []
        trace.add(f"agent.{tool_role}", retry=bool(failures))
        # Every attempt gets its own tool-call budget (a retry must not start with the first attempt's calls already spent)
        # but all attempts share one evidence ledger.
        attempt_dispatcher = ToolDispatcher(context.for_agent(tool_role), dataset, ledger)
        return _normalise(await runner.run_tool_agent(
            role=tool_role, instructions=role_prompt(tool_role), user_input=tool_agent_input(context, payload, failures),
            dispatcher=attempt_dispatcher, output_type=EvidenceBrief, run=run,
        ), ledger)

    async def checked_brief(failures: list[str] | None) -> tuple[EvidenceBrief | None, list[str]]:
        """One brief attempt: (brief, problems). A reply in the wrong shape counts as a problem, not a crash."""
        try:
            candidate = await produce_brief(failures)
        except InvalidOutput as error:
            trace.add("brief.validate", passed=False, issues=[f"V1: {error}"])
            return None, [f"V1: the brief did not match the required format ({error})"]
        problems = _failures(validator.validate(candidate))
        if not candidate.facts:
            problems.append("V15: the brief has no facts. Call the evidence tools first and cite what they return.")
        trace.add("brief.validate", passed=not problems, issues=problems)
        return candidate, problems

    try:
        if not from_cache:
            brief, problems = await checked_brief(None)
            if problems:
                trace.retries += 1
                brief, problems = await checked_brief(problems)
                if problems:
                    return _fallback(payload, trace, "brief_validation_failed")
    except RunnerError as error:
        return _fallback(payload, trace, f"{tool_role}_failed: {error}")
    if brief.scope != "in_scope":
        return _fallback(payload, trace, f"scope_{brief.scope}", brief)
    run.brief = brief

    writer_type = RefereeAnswer if tool_role == "referee" else Explanation

    async def write(failures: list[str] | None) -> Explanation | RefereeAnswer:
        run.failures = failures or []
        trace.add("agent.storyteller", retry=bool(failures))
        return await runner.run_writer(
            role="storyteller", instructions=storyteller_instructions(context), user_input=storyteller_input(context, brief, failures),
            output_type=writer_type, run=run,
        )

    async def checked_answer(failures: list[str] | None):
        try:
            candidate = await write(failures)
        except InvalidOutput as error:
            trace.add("answer.validate", passed=False, issues=[f"V1: {error}"])
            return None, [f"V1: the answer did not match the required format ({error})"]
        candidate = _normalise_answer(candidate, brief, ledger)
        problems = _failures(validator.validate(candidate))
        trace.add("answer.validate", passed=not problems, issues=problems)
        return candidate, problems

    try:
        answer, problems = await checked_answer(None)
        if problems:
            trace.retries += 1
            if any(item.split(":")[0] in EVIDENCE_FAILURES for item in problems):
                if from_cache:
                    # The cached brief is missing something the answer needs: drop it and run the full workflow once.
                    return await run_workflow(payload.model_copy(update={"cached_brief": None, "brief_cutoff_ms": None}), dataset, runner, critic_enabled=critic_enabled)
                # The answer needs a fact the brief lacks: one tool-capable re-run, then the Storyteller again.
                brief, brief_problems = await checked_brief(problems)
                if brief_problems:
                    return _fallback(payload, trace, "brief_validation_failed")
                run.brief = brief
            answer, problems = await checked_answer(problems)
            if problems:
                return _fallback(payload, trace, "answer_validation_failed", brief)

        if critic_enabled:
            run.answer = answer
            verdict = await runner.run_writer(
                role="critic", instructions=role_prompt("critic"),
                user_input=critic_input(context, brief, answer.model_dump_json(by_alias=True)), output_type=CriticVerdict, run=run,
            )
            trace.add("agent.critic", verdict=verdict.verdict)
            if verdict.verdict != "pass":
                if trace.retries:
                    if verdict.verdict == "unsupported_claim":
                        return _fallback(payload, trace, "critic_unsupported_claim", brief)
                else:
                    trace.retries += 1
                    note = [f"critic: {issue.kind} in sentence {issue.sentence}: {issue.note}" for issue in verdict.issues] or ["critic: revise the wording"]
                    answer, problems = await checked_answer(note)
                    if problems:
                        return _fallback(payload, trace, "answer_validation_failed", brief)
    except RunnerError as error:
        return _fallback(payload, trace, f"storyteller_failed: {error}", brief)

    evidence_ids = sorted({e for fact in brief.facts for e in fact.evidence_ids if e.startswith("e_")})
    response = AgentResponse(
        request_id=payload.request_id, status="ok", source=runner.source, workflow_version=WORKFLOW_VERSION,
        answer=_answer_text(answer)[:2400], evidence_ids=evidence_ids[:24], evidence_brief=brief,
        explanation=answer if isinstance(answer, Explanation) else None,
        referee_answer=answer if isinstance(answer, RefereeAnswer) else None,
        brief_cutoff_ms=brief_cutoff, brief_cached=from_cache,
    )
    return WorkflowResult(response, trace, ledger)
