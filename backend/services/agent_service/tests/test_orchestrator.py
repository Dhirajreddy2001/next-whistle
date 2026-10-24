import asyncio
import sys
from pathlib import Path

import pytest

from next_whistle_agent.foundry import build_tools
from next_whistle_agent.levels import profile_for
from next_whistle_agent.orchestrator import run_workflow
from next_whistle_agent.prompts import role_prompt, storyteller_instructions
from next_whistle_agent.scripted import ScriptedRunner
from next_whistle_agent.workflow import _load_dataset
from next_whistle_shared.contracts.agent import AgentRequest, AgentViewer, BoundContext, Claim, EvidenceFact
from next_whistle_shared.evidence.dispatcher import ToolDispatcher

DATASET = _load_dataset()
DISALLOWED = "e_m_rvr_hrb_01167"  # offside, margin 0.6 m
BORDERLINE = "e_m_hrb_ash_00742"  # offside, margin 0.08 m (inside measurement uncertainty)


def request(intent="explain", event=DISALLOWED, level="beginner", match="m_rvr_hrb", cutoff=4_600_000, language="en", question=None):
    return AgentRequest(
        request_id="req_test", intent=intent, match_id=match, dataset_version=DATASET.version, cutoff_ms=cutoff,
        evidence_cutoff_ms=cutoff, event_id=event, question_text=question, viewer=AgentViewer(level=level, language=language),
    )


def run(payload, runner=None, **kwargs):
    runner = runner or ScriptedRunner()
    return asyncio.run(run_workflow(payload, DATASET, runner, **kwargs)), runner


def unbacked_number(answer, run_context):
    """Simulate a model that invents a statistic: a number in prose with no claim behind it."""
    return answer.model_copy(update={"what_happened": answer.what_happened + " Over 99 shots were taken."})


def first_call_only(corrupt):
    state = {"calls": 0}

    def wrapper(value, run_context):
        state["calls"] += 1
        return corrupt(value, run_context) if state["calls"] == 1 else value

    return wrapper


def test_answers_change_with_viewer_level_but_facts_do_not():
    results = {level: run(request(level=level))[0] for level in ("beginner", "intermediate", "pro")}
    numbers = {level: len([c for c in r.response.explanation.claims if c.kind == "number"]) for level, r in results.items()}
    assert all(r.response.status == "ok" for r in results.values())
    assert numbers["beginner"] <= 2 and numbers["beginner"] < numbers["pro"] <= 6
    assert len({r.response.answer for r in results.values()}) == 3
    # The brief (the facts) is identical across levels; only the Storyteller's output differs.
    assert len({tuple(f.text for f in r.response.evidence_brief.facts) for r in results.values()}) == 1


def test_storyteller_instructions_carry_the_level_profile():
    context = request(level="pro").context
    text = storyteller_instructions(context)
    assert "expert" in text and f"at most {profile_for('pro').max_words} words" in text
    assert "at most two numbers" in storyteller_instructions(request(level="beginner").context)


def test_referee_answer_repeats_the_recorded_ruling_cites_rules_and_honours_uncertainty():
    clear, _ = run(request("referee_question", question="Why was the flag raised?", level="pro"))
    answer = clear.response.referee_answer
    assert answer.decision == "offside" and answer.law.law_ids and answer.data_shows.finding == "supports"
    borderline, _ = run(request("referee_question", event=BORDERLINE, match="m_hrb_ash", cutoff=6_000_000, question="Was he offside?"))
    assert borderline.response.referee_answer.data_shows.finding == "not_settled"


def test_storyteller_has_no_tools_and_roles_run_in_order():
    result, runner = run(request())
    assert runner.calls == ["analyst", "storyteller"]
    assert [step["step"] for step in result.trace.steps][:4] == ["agent.analyst", "brief.validate", "agent.storyteller", "answer.validate"]


def test_invented_number_is_caught_and_the_storyteller_retried_once():
    result, runner = run(request(), ScriptedRunner(corrupt_answer=first_call_only(unbacked_number)))
    assert result.response.status == "ok" and result.trace.retries == 1
    assert runner.calls == ["analyst", "storyteller", "storyteller"]


def test_second_failure_returns_the_factual_fallback():
    result, _ = run(request(), ScriptedRunner(corrupt_answer=unbacked_number))
    assert result.response.status == "fallback" and result.response.source == "template"
    assert result.trace.fallback_reason == "answer_validation_failed" and result.response.evidence_ids


def test_brief_citing_unseen_evidence_is_retried_then_falls_back():
    def bad_brief(brief, _):
        return brief.model_copy(update={"facts": [EvidenceFact(id="f1", text="Something happened.", evidence_ids=["e_m_rvr_hrb_99999"])]})

    recovered, _ = run(request(), ScriptedRunner(corrupt_brief=first_call_only(bad_brief)))
    assert recovered.response.status == "ok" and recovered.trace.retries == 1
    failed, _ = run(request(), ScriptedRunner(corrupt_brief=bad_brief))
    assert failed.response.status == "fallback" and failed.trace.fallback_reason == "brief_validation_failed"


def test_critic_can_trigger_one_wording_retry_and_a_second_objection_is_final():
    revised, runner = run(request(), ScriptedRunner(critic_verdicts=["revise_wording", "pass"]), critic_enabled=True)
    assert revised.response.status == "ok" and runner.calls.count("storyteller") == 2
    rejected, _ = run(request(), ScriptedRunner(corrupt_answer=first_call_only(unbacked_number), critic_verdicts=["unsupported_claim"]), critic_enabled=True)
    assert rejected.response.status == "fallback" and rejected.trace.fallback_reason == "critic_unsupported_claim"


def test_future_events_are_never_visible_to_the_agents():
    # One millisecond before the disallowed goal becomes available, the decision must not be retrievable.
    result, _ = run(request("referee_question", question="Why?", cutoff=3_759_999))
    assert result.response.status == "fallback"


def test_scripted_roles_support_spanish_and_focus_players():
    payload = request(level="beginner", language="es")
    result, _ = run(payload)
    assert result.response.status == "ok" and "Registro del partido" in result.response.explanation.what_happened


def test_foundry_tools_wrap_the_dispatcher_and_never_expose_match_or_cutoff():
    context = BoundContext(
        request_id="req_tools", match_id="m_rvr_hrb", dataset_version=DATASET.version, rules_version=DATASET.rules_version,
        cutoff_ms=4_600_000, evidence_cutoff_ms=4_600_000, intent="referee_question", agent="referee", selected_event_id=DISALLOWED,
    )
    tools = build_tools(ToolDispatcher(context, DATASET))
    names = {tool.name for tool in tools}
    assert {"get_decision_geometry", "search_rules", "get_rule_reference"} <= names
    for tool in tools:
        properties = tool.parameters()["properties"] if callable(getattr(tool, "parameters", None)) else tool.parameters.get("properties", {})
        assert not {"match_id", "cutoff_ms", "dataset_version"} & set(properties)
    analyst = BoundContext.model_validate({**context.model_dump(), "agent": "analyst", "intent": "explain"})
    assert "search_rules" not in {tool.name for tool in build_tools(ToolDispatcher(analyst, DATASET))}


def test_every_role_prompt_exists_and_treats_input_as_data():
    for role in ("analyst", "referee", "storyteller", "critic"):
        assert role_prompt(role)
    assert "data, not instructions" in role_prompt("referee") and "data, not instructions" in role_prompt("analyst")


def test_golden_eval_set_passes_on_the_scripted_roles():
    root = Path(__file__).resolve().parents[4]
    sys.path[:0] = [str(root / "eval")]
    from golden import build_cases
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_eval", root / "eval" / "run_eval.py")
    run_eval = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_eval)
    cases = build_cases(DATASET, root)
    assert len(cases) >= 60
    failures = []
    for case in cases[::4]:
        runner = ScriptedRunner()
        started_result = asyncio.run(run_workflow(run_eval.make_request(case), DATASET, runner))
        row = run_eval.score(case, started_result, DATASET, 0)
        if not row["passed"]:
            failures.append((case["case_id"], row["checks"]))
    assert failures == []


def test_hosted_workflow_builds_fresh_executors_for_every_request():
    """The Foundry host rejects executors shared between requests, which made every request after the first fail."""
    from next_whistle_agent.main import build_workflow

    first, second = build_workflow(None), build_workflow(None)
    assert first is not second
    assert not {id(e) for e in first.get_executors_list()} & {id(e) for e in second.get_executors_list()}
