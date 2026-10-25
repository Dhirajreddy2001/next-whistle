from types import SimpleNamespace
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
from next_whistle_shared.contracts.agent import AgentRequest, AgentViewer, BoundContext, Claim, EvidenceBrief, EvidenceFact
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


# ---- brief cache: the level-free brief is reused, only the Storyteller runs ------------------------------------------

def fresh_brief(level="beginner", **kwargs):
    result, runner = run(request(level=level, **kwargs))
    return result.response.evidence_brief, result.response.brief_cutoff_ms, result


def with_brief(brief, brief_cutoff, level="pro", cutoff=4_650_000, **kwargs):
    payload = request(level=level, cutoff=cutoff, **kwargs).model_copy(update={"cached_brief": brief, "brief_cutoff_ms": brief_cutoff})
    return AgentRequest.model_validate(payload.model_dump(by_alias=True))


def test_a_cached_brief_skips_retrieval_and_serves_any_level_and_language():
    brief, brief_cutoff, fresh = fresh_brief("beginner")
    assert brief_cutoff == 4_600_000 and fresh.response.brief_cached is False
    answers = {}
    for level, language in (("pro", "en"), ("intermediate", "en"), ("beginner", "es")):
        payload = with_brief(brief, brief_cutoff, level=level, language=language)
        result, runner = run(payload)
        assert runner.calls == ["storyteller"]  # no Analyst: no tool calls, no retrieval tokens
        assert result.response.status == "ok" and result.response.brief_cached is True and result.response.brief_cutoff_ms == 4_600_000
        assert result.trace.steps[0] == {"step": "brief.cache", "used": True}
        answers[(level, language)] = result.response.answer
    assert len(set(answers.values())) == 3
    assert answers[("beginner", "es")].startswith("Disallowed") or "Registro" in run(with_brief(brief, brief_cutoff, language="es", level="beginner"))[0].response.explanation.what_happened


def test_a_cached_brief_that_cites_evidence_not_yet_visible_is_ignored():
    brief, _, _ = fresh_brief()
    result, runner = run(with_brief(brief, 3_000_000, cutoff=4_600_000))  # claims to be from before its own evidence existed
    assert runner.calls == ["analyst", "storyteller"] and result.response.brief_cached is False
    assert result.trace.steps[0] == {"step": "brief.cache", "used": False}


def test_a_cached_brief_the_answer_cannot_stand_on_falls_back_to_one_full_run():
    def invented_stat(answer, _):
        claim = Claim(id="c9", kind="number", text="Shots: 99", stat_ref="team.shots.t_unknown", value=99, evidence_ids=[DISALLOWED])
        return answer.model_copy(update={"claims": [*answer.claims, claim]})

    brief, brief_cutoff, _ = fresh_brief()
    result, runner = run(with_brief(brief, brief_cutoff), ScriptedRunner(corrupt_answer=first_call_only(invented_stat)))
    assert runner.calls == ["storyteller", "analyst", "storyteller"] and result.response.status == "ok"
    assert result.response.brief_cached is False


@pytest.mark.parametrize("change, message", [
    ({"brief_cutoff_ms": None}, "together"),
    ({"brief_cutoff_ms": 9_999_999}, "newer than the viewer"),
    ({"question_text": "Why?"}, "Free-text"),
    ({"event_id": "e_m_rvr_hrb_00574"}, "different request"),
    ({"intent": "recap", "event_id": None}, "different request"),
])
def test_cached_brief_contract_rejects_unsafe_combinations(change, message):
    brief, brief_cutoff, _ = fresh_brief()
    data = request(cutoff=4_650_000).model_dump(by_alias=True)
    data.update({"cachedBrief": brief.model_dump(by_alias=True), "briefCutoffMs": brief_cutoff})
    for key, value in change.items():
        data[{"brief_cutoff_ms": "briefCutoffMs", "question_text": "questionText", "event_id": "eventId", "intent": "intent"}[key]] = value
    data["context"] = None
    with pytest.raises(ValueError, match=message):
        AgentRequest.model_validate(data)


def test_model_slips_on_scope_and_significance_are_tidied_without_touching_facts():
    def sloppy(brief, _):
        return brief.model_copy(update={"scope": "general_law", "uncertainty": [], "significance": brief.significance.model_copy(update={"established": False, "text": "", "fact_ids": []})})

    result, runner = run(request(), ScriptedRunner(corrupt_brief=sloppy))
    brief = result.response.evidence_brief
    assert result.response.status == "ok" and runner.calls == ["analyst", "storyteller"]  # no wasted retry
    assert brief.scope == "in_scope" and brief.uncertainty and brief.significance.established is False
    assert [f.text for f in brief.facts]  # facts are exactly what the model produced


# ---- real-model failure modes: malformed output, empty briefs, skipped tools -----------------------------------------

class FlakyRunner(ScriptedRunner):
    """A scripted runner that replies in the wrong shape a set number of times, like a real model sometimes does."""

    def __init__(self, bad_briefs=0, bad_answers=0):
        super().__init__()
        self.bad_briefs, self.bad_answers = bad_briefs, bad_answers

    async def run_tool_agent(self, **kwargs):
        if self.bad_briefs > 0:
            self.bad_briefs -= 1
            self.calls.append(kwargs["role"])
            from next_whistle_agent.scripted import InvalidOutput
            raise InvalidOutput("EvidenceBrief: significance field required")
        return await super().run_tool_agent(**kwargs)

    async def run_writer(self, **kwargs):
        if kwargs["role"] == "storyteller" and self.bad_answers > 0:
            self.bad_answers -= 1
            self.calls.append("storyteller")
            from next_whistle_agent.scripted import InvalidOutput
            raise InvalidOutput("Explanation: claims.0 Number claims need a value")
        return await super().run_writer(**kwargs)


def test_malformed_model_output_is_retried_once_instead_of_crashing():
    result, runner = run(request(), FlakyRunner(bad_answers=1))
    assert result.response.status == "ok" and result.trace.retries == 1 and runner.calls == ["analyst", "storyteller", "storyteller"]
    result, runner = run(request(), FlakyRunner(bad_briefs=1))
    assert result.response.status == "ok" and runner.calls == ["analyst", "analyst", "storyteller"]


def test_two_malformed_replies_fall_back_with_a_reason():
    result, _ = run(request(), FlakyRunner(bad_answers=2))
    assert result.response.status == "fallback" and result.trace.fallback_reason == "answer_validation_failed"
    issues = [issue for step in result.trace.steps for issue in step.get("issues", [])]
    assert any(issue.startswith("V1:") and "claims.0" in issue for issue in issues)  # the reason is recorded, not lost
    result, _ = run(request(), FlakyRunner(bad_briefs=2))
    assert result.response.status == "fallback" and result.trace.fallback_reason == "brief_validation_failed"


def test_a_brief_without_facts_is_rejected_even_though_every_cited_item_is_valid():
    def empty(brief, _):
        return brief.model_copy(update={"facts": [], "involvement": {}, "factors": []})

    result, runner = run(request(), ScriptedRunner(corrupt_brief=first_call_only(empty)))
    assert result.response.status == "ok" and result.trace.retries == 1 and runner.calls == ["analyst", "analyst", "storyteller"]
    result, _ = run(request(), ScriptedRunner(corrupt_brief=empty))
    assert result.response.status == "fallback" and result.trace.fallback_reason == "brief_validation_failed"


def test_foundry_runner_needs_tool_calls_and_maps_bad_replies_to_retryable_failures():
    from next_whistle_agent.foundry import FoundryRunner
    from next_whistle_agent.scripted import InvalidOutput
    from next_whistle_shared.contracts.agent import Explanation

    class Reply:
        text = '{"headline": "x"}'

        @property
        def value(self):
            return Explanation.model_validate_json(self.text)  # raises: required fields are missing

    class Fake(FoundryRunner):
        def __init__(self):
            super().__init__(None, "m", "m", "http://unused")
            self.phases = []

        async def _call(self, role, model, instructions, user_input, tools, temperature, response_format):
            self.phases.append("tools" if tools else "structured")
            return Reply()

    runner = Fake()
    context = BoundContext(
        request_id="req_f", match_id="m_rvr_hrb", dataset_version=DATASET.version, rules_version=DATASET.rules_version,
        cutoff_ms=4_600_000, evidence_cutoff_ms=4_600_000, intent="explain", agent="analyst", selected_event_id=DISALLOWED,
    )
    dispatcher = ToolDispatcher(context, DATASET)
    with pytest.raises(InvalidOutput, match="did not call any evidence tool"):
        asyncio.run(runner.run_tool_agent(role="analyst", instructions="i", user_input="u", dispatcher=dispatcher, output_type=EvidenceBrief, run=SimpleNamespace(context=context, payload=SimpleNamespace(question_text="why?", question_id=None))))
    assert runner.phases == ["tools"]  # it never moved on to writing a brief without evidence
    with pytest.raises(InvalidOutput, match="Explanation"):
        asyncio.run(runner.run_writer(role="storyteller", instructions="i", user_input="u", output_type=Explanation, run=None))


def test_a_brief_may_carry_raw_numbers_from_tool_results_but_the_answer_may_not_invent_them():
    def numeric_facts(brief, _):
        extra = EvidenceFact(id="f9", text="The attacker was 0.6 metres beyond the line at 3666000 ms.", evidence_ids=[DISALLOWED])
        return brief.model_copy(update={"facts": [*brief.facts, extra]})

    result, runner = run(request(), ScriptedRunner(corrupt_brief=numeric_facts))
    assert result.response.status == "ok" and runner.calls == ["analyst", "storyteller"]  # no retry for numbers in the brief
    result, _ = run(request(), ScriptedRunner(corrupt_answer=unbacked_number))
    assert result.response.status == "fallback"  # the answer is still held to the number rule


def test_the_storyteller_is_told_exactly_which_numbers_it_may_state():
    from next_whistle_agent.prompts import allowed_numbers

    explain_brief, _, _ = fresh_brief("pro")
    assert "statRef team.shots" in allowed_numbers(explain_brief) and "NONE" not in allowed_numbers(explain_brief)
    bare = explain_brief.model_copy(update={"stats": []})
    assert "NONE" in allowed_numbers(bare) and "no digits" in allowed_numbers(bare)
    referee_brief = run(request("referee_question", question="Why?", level="pro"))[0].response.evidence_brief
    assert f"geometryRef geometry.{DISALLOWED}" in allowed_numbers(referee_brief) and "0.6" in allowed_numbers(referee_brief)


def test_a_brief_may_cite_rule_and_snapshot_ids_that_tools_returned_but_not_ones_it_invented():
    def cite(extra):
        def corrupt(brief, _):
            fact = brief.facts[0].model_copy(update={"evidence_ids": [*brief.facts[0].evidence_ids, *extra]})
            return brief.model_copy(update={"facts": [fact, *brief.facts[1:]]})
        return corrupt

    referee = request("referee_question", question="Why?")
    ok, runner = run(referee, ScriptedRunner(corrupt_brief=cite(["r_law11_offside"])))
    assert ok.response.status == "ok" and runner.calls == ["referee", "storyteller"]  # a rule the Referee retrieved
    assert all(e.startswith("e_") for e in ok.response.evidence_ids)  # only event ids go back to the Core
    bad, _ = run(referee, ScriptedRunner(corrupt_brief=cite(["r_law99_invented"])))
    assert bad.response.status == "fallback" and bad.trace.fallback_reason == "brief_validation_failed"


def test_involvement_is_computed_from_the_cited_events_not_trusted_from_the_model():
    def garbled(brief, _):
        return brief.model_copy(update={"involvement": {"playerId": ["x"], "role": ["y"]}})

    result, _ = run(request(), ScriptedRunner(corrupt_brief=garbled))
    involvement = result.response.evidence_brief.involvement
    assert result.response.status == "ok" and "playerId" not in involvement and all(key.startswith("p_") for key in involvement)


def test_a_retry_gets_a_fresh_tool_budget_instead_of_inheriting_the_spent_one():
    class Greedy(ScriptedRunner):
        """Uses the whole tool budget on the first attempt, then proves the second attempt can still call tools."""

        def __init__(self):
            super().__init__()
            self.budgets = []

        async def run_tool_agent(self, **kwargs):
            dispatcher = kwargs["dispatcher"]
            self.budgets.append(dispatcher.calls)
            for _ in range(dispatcher.context.max_tool_calls):
                dispatcher.call("get_match_state", {})
            if len(self.budgets) == 1:
                raise __import__("next_whistle_agent.scripted", fromlist=["InvalidOutput"]).InvalidOutput("first attempt malformed")
            dispatcher.calls = 0  # the scripted brief below needs its own few calls
            return await super().run_tool_agent(**kwargs)

    result, runner = run(request(), Greedy())
    assert runner.budgets == [0, 0] and result.response.status == "ok"


def test_referee_answers_carry_the_recorded_ruling_and_only_the_cited_laws_whatever_the_model_wrote():
    def wordy(answer, _):
        return answer.model_copy(update={"decision": "The flag was raised because the attacker was ahead of the line.", "law": answer.law.model_copy(update={"law_ids": ["r_law11_offside", "r_law99_made_up"]})})

    result, _ = run(request("referee_question", question="Why?"), ScriptedRunner(corrupt_answer=wordy))
    answer = result.response.referee_answer
    assert result.response.status == "ok" and answer.decision == "offside" and "r_law99_made_up" not in answer.law.law_ids


def test_a_reference_on_a_plain_fact_claim_is_dropped_instead_of_failing_the_whole_answer():
    claim = Claim(id="c1", kind="fact", text="A fact.", geometry_ref="geometry.e_x", value=None)
    assert claim.geometry_ref is None and claim.value is None
    assert Claim(id="c2", kind="number", text="n", stat_ref="team.shots.t_a", value=3).value == 3  # real number claims are untouched


def test_tool_results_give_names_and_findings_but_no_raw_coordinates_to_quote():
    context = BoundContext(
        request_id="req_t", match_id="m_rvr_hrb", dataset_version=DATASET.version, rules_version=DATASET.rules_version, cutoff_ms=4_600_000,
        evidence_cutoff_ms=4_600_000, intent="referee_question", agent="referee", selected_event_id=DISALLOWED,
    )
    dispatcher = ToolDispatcher(context, DATASET)
    event = dispatcher.call("get_event_evidence", {"event_id": DISALLOWED, "include_build_up": True, "include_snapshots": False}).data
    assert event["event"]["playerName"] and event["event"]["teamName"] and "start" not in event["event"] and "authored_margin_m" not in str(event)
    assert len(event["buildUp"]) <= 5 and all("start" not in row and "end" not in row for row in event["buildUp"])
    geometry = dispatcher.call("get_decision_geometry", {"event_id": DISALLOWED}).data
    assert geometry["marginM"] == 0.6 and geometry["attackerName"] and "attackerPosition" not in geometry and "ballX" not in geometry
    rules = dispatcher.call("get_rule_reference", {"incident_tag": "offside"}).data["rules"]
    assert len(rules) <= 3 and set(rules[0]) == {"ruleId", "lawNumber", "lawTitle", "summary"}


def test_an_explanation_may_only_cite_a_rule_a_tool_actually_returned():
    from next_whistle_shared.contracts.agent import RuleExplanation

    def cites(rule_id):
        return lambda answer, _: answer.model_copy(update={"rule": RuleExplanation(rule_id=rule_id, text="The offside rule applies.")})

    invented, _ = run(request(), ScriptedRunner(corrupt_answer=cites("Law 11")))  # the Analyst has no rule tool
    assert invented.response.status == "ok" and invented.response.explanation.rule is None


# ---- general Laws questions: explain only the rule the Core found -----------------------------------------------------

def law_request(level="beginner", language="en", text="when is a red card given", excerpts=None):
    import json
    from next_whistle_shared.contracts.agent import RuleExcerpt

    rules = json.loads((Path(__file__).resolve().parents[4] / "data/rules/v1.2.0/rules.json").read_text(encoding="utf-8"))
    chosen = excerpts or [next(r for r in rules if r["rule_id"] == "r_law12_red_card")]
    return AgentRequest(
        request_id="req_law", intent="referee_question", match_id="m_rvr_hrb", dataset_version=DATASET.version, cutoff_ms=4_600_000,
        evidence_cutoff_ms=4_600_000, question_text=text, viewer=AgentViewer(level=level, language=language),
        rule_excerpts=[RuleExcerpt(rule_id=r["rule_id"], law_number=r["law_number"], title=r["law_title"], summary=r["summary"], plain=r.get("plain")) for r in chosen],
    )


from pathlib import Path  # noqa: E402


def test_a_rule_question_runs_one_storyteller_call_and_no_retrieval():
    result, runner = run(law_request())
    assert result.response.status == "ok" and runner.calls == ["storyteller"]
    assert result.response.law_explanation.law_ids == ["r_law12_red_card"] and "red card" in result.response.answer.lower()
    pro, _ = run(law_request(level="pro"))
    assert pro.response.answer != result.response.answer  # the level changes the wording


def test_a_rule_answer_may_cite_only_the_rules_it_was_given_and_state_only_numbers_from_them():
    from next_whistle_shared.contracts.agent import LawExplanation

    def sloppy(answer, _):
        return LawExplanation(headline="Red cards", answer="A red card is shown for serious foul play, and a goalkeeper may hold the ball 8 seconds.", law_ids=["r_law12_red_card", "r_law99_invented"])

    result, runner = run(law_request(), ScriptedRunner(corrupt_answer=sloppy))
    assert result.response.status == "fallback" and result.trace.fallback_reason == "answer_validation_failed" and runner.calls == ["storyteller", "storyteller"]
    assert any("without a number claim" in issue for step in result.trace.steps for issue in step.get("issues", []))
    from next_whistle_shared.contracts.agent import LawExplanation as L
    fine, _ = run(law_request(), ScriptedRunner(corrupt_answer=lambda a, _: L(headline="Law 12", answer="Here is the rule.", law_ids=["r_law12_red_card", "r_law99_invented"])))
    assert fine.response.law_explanation.law_ids == ["r_law12_red_card"]  # an id it was not given is dropped


def test_numbers_that_the_rule_text_contains_are_allowed():
    import json
    from next_whistle_shared.contracts.agent import LawExplanation

    rules = json.loads((Path(__file__).resolve().parents[4] / "data/rules/v1.2.0/rules.json").read_text(encoding="utf-8"))
    corner = [next(r for r in rules if r["rule_id"] == "r_law17_corner_kick")]
    ok, _ = run(law_request(text="how far must defenders stand at a corner", excerpts=corner), ScriptedRunner(corrupt_answer=lambda a, _: LawExplanation(headline="Corner kick", answer="Opponents must stay at least 9.15 metres away.", law_ids=["r_law17_corner_kick"])))
    assert ok.response.status == "ok"


def test_rule_requests_cannot_smuggle_in_an_event_or_a_cached_brief():
    import pytest as _pytest

    base = law_request().model_dump(by_alias=True)
    with _pytest.raises(ValueError, match="typed general question"):
        AgentRequest.model_validate({**base, "eventId": DISALLOWED, "context": None})
    with _pytest.raises(ValueError, match="typed general question"):
        AgentRequest.model_validate({**base, "intent": "explain", "context": None})


def test_storyteller_is_given_only_as_many_statistics_as_the_level_allows():
    from next_whistle_agent.prompts import for_viewer

    result, _ = run(request(intent="recap", event=None, level="pro"))
    brief = result.response.evidence_brief
    assert len(brief.stats) >= 2
    beginner = for_viewer(SimpleNamespace(level="beginner"), brief)
    assert len(beginner.stats) <= 2
    assert all(".goals." in stat.stat_ref for stat in beginner.stats) or len(brief.stats) < 2  # goals come first
    assert len(brief.stats) >= len(beginner.stats)  # the stored brief keeps everything


def test_a_score_written_from_the_leaders_side_is_accepted_but_a_wrong_one_is_not():
    def with_score(text):
        return lambda answer, ctx: answer.model_copy(update={"what_happened": answer.what_happened + f" It stands at {text}."})

    result, _ = run(request())
    state = result.ledger.visible_events["__match_state__"]["score"]
    home, away = state["home"], state["away"]
    ok, _ = run(request(), ScriptedRunner(corrupt_answer=with_score(f"{max(home, away)}–{min(home, away)}")))
    assert ok.response.status == "ok" and ok.trace.retries == 0
    bad, _ = run(request(), ScriptedRunner(corrupt_answer=with_score("9–9")))
    assert bad.trace.retries == 1
