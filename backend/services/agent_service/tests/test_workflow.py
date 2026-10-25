from pathlib import Path

from next_whistle_shared.contracts.agent import AgentEvidenceBrief, AgentRequest, BoundContext
from next_whistle_shared.evidence.dataset import load_evidence_dataset
from next_whistle_shared.evidence.dispatcher import ToolDispatcher
from next_whistle_shared.evidence.tool_schemas import tool_json_schemas
from next_whistle_agent.workflow import fake_answer, run_fake_workflow


ROOT = Path(__file__).resolve().parents[4]
DATASET = load_evidence_dataset(ROOT / "data/datasets/v1.2.0")


def context(*, cutoff=4_000_000, match="m_rvr_hrb", intent="explain", agent="analyst", event=None):
    return BoundContext(
        request_id="req_test", match_id=match, dataset_version="1.2.0", rules_version="1.2.0",
        cutoff_ms=cutoff, evidence_cutoff_ms=cutoff, intent=intent, agent=agent,
        selected_event_id=event,
    )


def test_event_is_visible_at_cutoff_equality():
    event_id = "e_m_rvr_hrb_00574"
    before = ToolDispatcher(context(cutoff=1_805_999, event=event_id), DATASET).call(
        "get_event_evidence", {"event_id": event_id}
    )
    at_boundary = ToolDispatcher(context(cutoff=1_806_000, event=event_id), DATASET).call(
        "get_event_evidence", {"event_id": event_id}
    )
    assert not before.ok and before.error.code == "not_visible"
    assert at_boundary.ok
    assert at_boundary.data["event"]["availableMs"] == 1_806_000


def test_var_delay_boundary_changes_score_only_when_decision_is_visible():
    event_id = "e_m_rvr_hrb_01167"
    before = ToolDispatcher(context(cutoff=3_759_999), DATASET).call("get_match_state", {})
    at_boundary = ToolDispatcher(context(cutoff=3_760_000), DATASET).call("get_match_state", {})
    assert before.ok and at_boundary.ok
    assert before.data["score"] == {"home": 1, "away": 1}
    assert at_boundary.data["score"] == {"home": 1, "away": 0}
    assert not ToolDispatcher(context(cutoff=3_759_999), DATASET).call(
        "get_event_evidence", {"event_id": event_id}
    ).ok


def test_tool_allow_list_denies_cross_role_tools():
    analyst = ToolDispatcher(context(agent="analyst"), DATASET)
    storyteller = ToolDispatcher(context(agent="storyteller"), DATASET)
    referee = ToolDispatcher(context(intent="decision", agent="referee"), DATASET)
    assert analyst.call("get_decision_geometry", {"event_id": "e_m_rvr_hrb_01167"}).error.code == "unknown_tool"
    assert storyteller.call("get_match_state", {}).error.code == "unknown_tool"
    assert referee.call("find_similar_events", {"event_id": "e_m_rvr_hrb_01167"}).error.code == "unknown_tool"


def test_tool_schemas_are_strict_and_exclude_bound_context_fields():
    for declaration in tool_json_schemas():
        params = declaration["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])
        assert not {"match_id", "cutoff_ms", "dataset_version"} & set(params["properties"])


def test_dispatcher_enforces_agent_call_limit():
    dispatcher = ToolDispatcher(context(), DATASET)
    for _ in range(6):
        assert dispatcher.call("get_match_state", {}).ok
    denied = dispatcher.call("get_match_state", {})
    assert not denied.ok and denied.error.code == "tool_limit_reached"


def test_model_supplied_match_or_cutoff_cannot_widen_bound_context():
    dispatcher = ToolDispatcher(context(cutoff=3_000_000), DATASET)
    bad_match = dispatcher.call("get_event_evidence", {"event_id": "e_m_hrb_ash_00001", "match_id": "m_hrb_ash"})
    bad_cutoff = dispatcher.call("get_event_evidence", {"event_id": "e_m_rvr_hrb_01167", "cutoff_ms": 4_000_000})
    future_event = dispatcher.call("get_event_evidence", {"event_id": "e_m_rvr_hrb_01167"})
    assert not bad_match.ok and bad_match.error.code == "invalid_arguments"
    assert not bad_cutoff.ok and bad_cutoff.error.code == "invalid_arguments"
    assert not future_event.ok and future_event.error.code == "not_visible"
    assert dispatcher.context.match_id == "m_rvr_hrb" and dispatcher.context.cutoff_ms == 3_000_000


def test_referee_and_analyst_fake_workflow_returns_structured_response():
    request = AgentRequest(
        request_id="req_workflow", intent="decision", match_id="m_rvr_hrb", dataset_version="1.2.0",
        cutoff_ms=3_760_000, evidence_cutoff_ms=3_760_000, event_id="e_m_rvr_hrb_01167",
    )
    response = run_fake_workflow(request, DATASET)
    assert response.source == "fake"
    assert response.referee_answer is not None
    assert response.evidence_brief is not None
    assert "offside" in response.referee_answer.decision


def test_fake_analyst_workflow_uses_bound_evidence_tools():
    request = AgentRequest(
        request_id="req_analyst", intent="explain", match_id="m_rvr_hrb", dataset_version="1.2.0",
        cutoff_ms=1_806_000, evidence_cutoff_ms=1_806_000, event_id="e_m_rvr_hrb_00574",
    )
    response = run_fake_workflow(request, DATASET)
    assert response.source == "fake"
    assert response.explanation is not None
    assert response.evidence_brief.facts[0].evidence_ids == ["e_m_rvr_hrb_00574"]


def test_legacy_flat_brief_remains_supported():
    request = AgentRequest(
        request_id="req_legacy", intent="explain", match_id="m_rvr_hrb", dataset_version="1.2.0",
        cutoff_ms=1000, evidence_cutoff_ms=1000,
        evidence_brief=AgentEvidenceBrief(headline="Shot", summary="Saved.", facts=["Keeper saved it."], evidence_ids=["e_m_rvr_hrb_00001"]),
    )
    assert "Keeper saved it" in fake_answer(request)



def test_referee_can_search_rules_and_ledger_records_returned_rules():
    from next_whistle_shared.contracts.agent import BoundContext
    from next_whistle_shared.evidence.dispatcher import ToolDispatcher
    from next_whistle_agent.workflow import _load_dataset

    dataset = _load_dataset()
    context = BoundContext(
        request_id="req_rules", match_id="m_rvr_hrb", dataset_version=dataset.version,
        rules_version=dataset.rules_version, cutoff_ms=6_000_000, evidence_cutoff_ms=6_000_000,
        intent="referee_question", agent="referee",
    )
    dispatcher = ToolDispatcher(context, dataset)
    result = dispatcher.call("search_rules", {"query": "second yellow card", "max_results": 2})
    assert result.ok and result.data["rules"][0]["ruleId"] == "r_law12_second_caution"
    assert "r_law12_second_caution" in dispatcher.ledger.rules
    analyst_context = BoundContext(
        request_id="req_rules", match_id="m_rvr_hrb", dataset_version=dataset.version,
        rules_version=dataset.rules_version, cutoff_ms=6_000_000, evidence_cutoff_ms=6_000_000,
        intent="explain", agent="analyst",
    )
    analyst = ToolDispatcher(analyst_context, dataset)
    assert analyst.call("search_rules", {"query": "offside", "max_results": 1}).error.code == "unknown_tool"


def test_prefetch_gathers_standard_evidence_through_the_bound_dispatcher():
    from next_whistle_agent.prefetch import prefetch

    event_id = "e_m_rvr_hrb_01167"  # a recorded offside decision
    ctx = context(cutoff=4_000_000, intent="decision", agent="referee", event=event_id)
    dispatcher = ToolDispatcher(ctx, DATASET)
    transcript = prefetch(dispatcher, ctx)
    tools = [step["tool"] for step in transcript]
    assert tools[:2] == ["get_match_state", "get_event_evidence"]
    assert "get_decision_geometry" in tools and "get_rule_reference" in tools
    assert "get_match_statistics" not in tools  # the referee role is not given that tool
    assert all(step["result"]["ok"] for step in transcript)
    assert event_id in dispatcher.ledger.evidence_ids  # what it fetched is what the validator will accept


def test_prefetch_recap_uses_the_last_visible_event_and_never_goes_past_the_cutoff():
    from next_whistle_agent.prefetch import prefetch

    ctx = context(cutoff=1_805_999, intent="recap", agent="analyst")
    dispatcher = ToolDispatcher(ctx, DATASET)
    prefetch(dispatcher, ctx)
    assert all(e["available_ms"] <= 1_805_999 for e in dispatcher.ledger.visible_events.values())
    assert "e_m_rvr_hrb_00574" not in dispatcher.ledger.evidence_ids  # available exactly at 1_806_000


def test_prefetch_gives_the_analyst_the_statistics():
    from next_whistle_agent.prefetch import prefetch

    ctx = context(cutoff=4_000_000, intent="recap", agent="analyst")
    tools = [step["tool"] for step in prefetch(ToolDispatcher(ctx, DATASET), ctx)]
    assert "get_match_statistics" in tools and "get_event_evidence" in tools
