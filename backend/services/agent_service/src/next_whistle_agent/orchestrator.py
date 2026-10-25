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
from .scripted import RunnerError

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


def _failures(report: ValidationReport) -> list[str]:
    return [f"{issue.check_id}: {issue.message}" for issue in report.issues if issue.severity == "error"]


def _squads_with_teams(dispatcher: ToolDispatcher) -> dict[str, dict]:
    match = dispatcher.match.match
    people = {p["player_id"]: p for side in (match["home"], match["away"]) for p in side["squad"]}
    people.update({side["team_id"]: {"name": side["name"]} for side in (match["home"], match["away"])})
    return people


def _answer_text(answer: Explanation | RefereeAnswer) -> str:
    if isinstance(answer, RefereeAnswer):
        return f"{answer.headline}. {answer.law.text} {answer.data_shows.text}".strip()
    return f"{answer.headline}. {answer.what_happened} {answer.why_it_matters}".strip()


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
        return await runner.run_tool_agent(
            role=tool_role, instructions=role_prompt(tool_role), user_input=tool_agent_input(context, payload, failures),
            dispatcher=agent_dispatcher, output_type=EvidenceBrief, run=run,
        )

    try:
        if not from_cache:
            brief = await produce_brief(None)
            report = validator.validate(brief)
            trace.add("brief.validate", passed=report.passed, issues=_failures(report))
            if not report.passed:
                trace.retries += 1
                brief = await produce_brief(_failures(report))
                report = validator.validate(brief)
                trace.add("brief.validate", passed=report.passed, issues=_failures(report))
                if not report.passed:
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

    try:
        answer = await write(None)
        report = validator.validate(answer)
        trace.add("answer.validate", passed=report.passed, issues=_failures(report))
        if not report.passed:
            failures = _failures(report)
            trace.retries += 1
            if from_cache and any(item.split(":")[0] in EVIDENCE_FAILURES for item in failures):
                # The cached brief is missing something the answer needs: drop it and run the full workflow once.
                return await run_workflow(payload.model_copy(update={"cached_brief": None, "brief_cutoff_ms": None}), dataset, runner, critic_enabled=critic_enabled)
            if any(item.split(":")[0] in EVIDENCE_FAILURES for item in failures):
                # The answer needs a fact the brief lacks: one tool-capable re-run, then the Storyteller again.
                brief = await produce_brief(failures)
                brief_report = validator.validate(brief)
                trace.add("brief.validate", passed=brief_report.passed, issues=_failures(brief_report))
                if not brief_report.passed:
                    return _fallback(payload, trace, "brief_validation_failed")
                run.brief = brief
            answer = await write(failures)
            report = validator.validate(answer)
            trace.add("answer.validate", passed=report.passed, issues=_failures(report))
            if not report.passed:
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
                    answer = await write(note)
                    report = validator.validate(answer)
                    trace.add("answer.validate", passed=report.passed, issues=_failures(report))
                    if not report.passed:
                        return _fallback(payload, trace, "answer_validation_failed", brief)
    except RunnerError as error:
        return _fallback(payload, trace, f"storyteller_failed: {error}", brief)

    evidence_ids = sorted({e for fact in brief.facts for e in fact.evidence_ids})
    response = AgentResponse(
        request_id=payload.request_id, status="ok", source=runner.source, workflow_version=WORKFLOW_VERSION,
        answer=_answer_text(answer)[:2400], evidence_ids=evidence_ids[:24], evidence_brief=brief,
        explanation=answer if isinstance(answer, Explanation) else None,
        referee_answer=answer if isinstance(answer, RefereeAnswer) else None,
        brief_cutoff_ms=brief_cutoff, brief_cached=from_cache,
    )
    return WorkflowResult(response, trace, ledger)
