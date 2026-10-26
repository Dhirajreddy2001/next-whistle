#!/usr/bin/env python3
"""Run the golden Q&A set through the agent workflow and score every answer.

  python eval/run_eval.py --mode fake                  # scripted roles, no model, free
  python eval/run_eval.py --mode foundry --max-usd 2 --confirm-spend [--limit 12]

Foundry mode calls real models and costs money. It refuses to start without --confirm-spend, --max-usd and
token prices, and it stops as soon as the running cost reaches the cap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend/shared/src"), str(ROOT / "backend/services/agent_service/src"), str(ROOT / "eval")]

from golden import LEVELS, build_cases  # noqa: E402
from next_whistle_agent.levels import profile_for  # noqa: E402
from next_whistle_agent.orchestrator import run_workflow  # noqa: E402
from next_whistle_agent.scripted import ScriptedRunner  # noqa: E402
from next_whistle_agent.workflow import _load_dataset  # noqa: E402
from next_whistle_shared.contracts.agent import AgentRequest, AgentViewer, Explanation, RefereeAnswer  # noqa: E402

_RULES = json.loads((ROOT / "data" / "rules" / "v1.2.0" / "rules.json").read_text(encoding="utf-8"))
ES_MARKERS = (" el ", " la ", " los ", " de ", " del ", "registro", "datos", "partido", "tarjeta", "gol", "decisión")
EST_INPUT_TOKENS, EST_OUTPUT_TOKENS = 9_000, 1_500  # per case, used only for the pre-run spend estimate


def make_request(case: dict) -> AgentRequest:
    if case.get("rule_id"):
        from next_whistle_shared.contracts.agent import RuleExcerpt
        rule = next(r for r in _RULES if r["rule_id"] == case["rule_id"])
        return AgentRequest(
            request_id=f"req_{abs(hash(case['case_id'])) % 10**9}", intent="referee_question", match_id=case["match_id"], dataset_version="1.3.0",
            cutoff_ms=case["cutoff_ms"], evidence_cutoff_ms=case["cutoff_ms"], question_text=case["question_text"],
            viewer=AgentViewer(level=case["level"], language=case["language"]),
            rule_excerpts=[RuleExcerpt(rule_id=rule["rule_id"], law_number=rule["law_number"], title=rule["law_title"], summary=rule["summary"], plain=rule.get("plain"))],
        )
    return AgentRequest(
        request_id=f"req_{abs(hash(case['case_id'])) % 10**9}", intent=case["intent"], match_id=case["match_id"],
        dataset_version="1.3.0", cutoff_ms=case["cutoff_ms"], evidence_cutoff_ms=case["cutoff_ms"], event_id=case.get("event_id"),
        question_text=case.get("question_text"), viewer=AgentViewer(level=case["level"], language=case["language"]),
    )


def _issues(result) -> list[str]:
    """The validator findings that made the workflow fall back, so a failed case says why."""
    return [issue for step in result.trace.steps for issue in step.get("issues", [])][-4:]


def score(case: dict, result, dataset, elapsed_ms: int) -> dict:
    response = result.response
    if case.get("rule_id"):  # a general Laws question: the answer must cite only the rule it was given and respect the level
        law = response.law_explanation
        profile = profile_for(case["level"])
        checks = {"completed": response.status == "ok", "rule_cited": bool(law) and law.law_ids == [case["rule_id"]],
                  "level_words": len(response.answer.split()) <= profile.max_words + 40}
        if case["language"].startswith("es") and law:
            checks["language"] = any(marker in f" {response.answer.lower()} " for marker in ES_MARKERS)
        return {"case_id": case["case_id"], "level": case["level"], "intent": "law", "passed": all(checks.values()), "checks": checks,
                "details": {"fallback_reason": result.trace.fallback_reason, "retries": result.trace.retries, "words": len(response.answer.split()), "issues": _issues(result)},
                "latency_ms": elapsed_ms, "answer": response.answer[:400]}
    answer = response.explanation or response.referee_answer
    checks: dict[str, bool] = {"completed": response.status == "ok"}
    details: dict = {"fallback_reason": result.trace.fallback_reason, "retries": result.trace.retries, "issues": _issues(result)}
    if answer is not None:
        profile = profile_for(case["level"])
        numbers = [c for c in answer.claims if c.kind == "number"]
        checks["level_numbers"] = len(numbers) <= profile.max_numbers
        checks["level_words"] = len(response.answer.split()) <= profile.max_words
        details.update(numbers=len(numbers), words=len(response.answer.split()))
    visible = {e["event_id"] for e in dataset.matches[case["match_id"]].events if e["available_ms"] <= case["cutoff_ms"]}
    checks["evidence_cited"] = bool(response.evidence_ids) and set(response.evidence_ids) <= visible
    if isinstance(answer, RefereeAnswer):
        event = next(e for e in dataset.matches[case["match_id"]].events if e["event_id"] == case["event_id"])
        checks["ruling_exact"] = answer.decision == event["decision"]["recorded_ruling"]
        rule_ids = {r["rule_id"] for r in dataset.rules}
        checks["rule_cited"] = bool(answer.law.law_ids) and set(answer.law.law_ids) <= rule_ids
        geometry = (response.evidence_brief.ruling.geometry or {}) if response.evidence_brief and response.evidence_brief.ruling else {}
        if geometry.get("withinUncertainty"):
            checks["uncertainty_honoured"] = answer.data_shows.finding == "not_settled"
    if case["language"].startswith("es") and answer is not None:
        checks["language"] = any(marker in f" {response.answer.lower()} " for marker in ES_MARKERS)
    return {"case_id": case["case_id"], "level": case["level"], "intent": case["intent"], "passed": all(checks.values()),
            "checks": checks, "details": details, "latency_ms": elapsed_ms, "answer": response.answer[:400]}


def adaptation_report(rows: list[dict]) -> dict:
    """Do answers actually change with the viewer's level? Compared per moment across the three levels."""
    groups: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if row["case_id"].endswith(".en") or ".t" in row["case_id"]:
            key = row["case_id"].rsplit(f".{row['level']}", 1)[0]
            groups[key][row["level"]] = row
    differing = comparable = 0
    for levels in groups.values():
        if set(levels) == set(LEVELS):
            comparable += 1
            differing += len({levels[level]["answer"] for level in LEVELS}) == 3
    return {"moments_compared": comparable, "moments_with_three_distinct_answers": differing}


async def main_async(args) -> int:
    dataset = _load_dataset()
    cases = build_cases(dataset, ROOT)
    if args.filter:
        cases = [c for c in cases if args.filter in c["case_id"]]
    if args.limit:
        step = max(1, len(cases) // args.limit)
        cases = cases[::step][: args.limit]
    cases = cases[args.skip:]
    live = args.mode == "foundry"
    price_in, price_out = float(os.getenv("PRICE_INPUT_PER_MTOK", "0")), float(os.getenv("PRICE_OUTPUT_PER_MTOK", "0"))
    if live:
        if not (args.confirm_spend and args.max_usd and price_in and price_out):
            print("Refusing to call a live model: pass --confirm-spend and --max-usd, and set PRICE_INPUT_PER_MTOK and PRICE_OUTPUT_PER_MTOK.")
            return 2
        estimate = len(cases) * (EST_INPUT_TOKENS * price_in + EST_OUTPUT_TOKENS * price_out) / 1e6
        print(f"{len(cases)} cases, estimated spend about ${estimate:.2f}, cap ${args.max_usd:.2f}")
        if estimate > args.max_usd:
            print("Estimate exceeds the cap. Lower --limit or raise --max-usd.")
            return 2
        from next_whistle_agent.foundry import FoundryRunner
        runner_cm = FoundryRunner.from_env()
    rows: list[dict] = []
    spent = 0.0
    started = datetime.now(timezone.utc)
    partial = ROOT / "eval" / "reports" / f"{started.strftime('%Y%m%dT%H%M%SZ')}-partial.jsonl"
    partial.parent.mkdir(parents=True, exist_ok=True)

    async def run_all(make_runner):
        nonlocal spent
        for case in cases:
            runner = make_runner()
            before = (runner.usage.input_tokens, runner.usage.output_tokens, runner.usage.calls)  # a live runner is shared, so measure the change
            t0 = time.perf_counter()
            result = await run_workflow(make_request(case), dataset, runner, critic_enabled=args.critic)
            row = score(case, result, dataset, int((time.perf_counter() - t0) * 1000))
            used = (runner.usage.input_tokens - before[0], runner.usage.output_tokens - before[1], runner.usage.calls - before[2])
            row["tokens"] = {"input": used[0], "output": used[1], "calls": used[2]}
            spent += (used[0] * price_in + used[1] * price_out) / 1e6
            rows.append(row)
            if live:  # progress is visible while a slow run is going, and a stopped run keeps what it scored
                print(f"[{len(rows)}/{len(cases)}] {'pass' if row['passed'] else 'FAIL'} {row['case_id']} {row['latency_ms']}ms ${spent:.3f}", flush=True)
                with partial.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            if live and spent >= args.max_usd:
                print(f"Spend cap reached (${spent:.2f}); stopping after {len(rows)} cases.")
                break

    if live:
        async with runner_cm as shared:
            await run_all(lambda: shared)
    else:
        await run_all(ScriptedRunner)

    passed = sum(r["passed"] for r in rows)
    by_check: dict[str, list[bool]] = defaultdict(list)
    by_level: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        by_level[row["level"]].append(row["passed"])
        for name, ok in row["checks"].items():
            by_check[name].append(ok)
    summary = {
        "mode": args.mode, "started": started.isoformat(), "cases": len(rows), "passed": passed, "pass_rate": round(passed / max(1, len(rows)), 3),
        "by_check": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_check.items())},
        "by_level": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_level.items())},
        "adaptation": adaptation_report(rows),
        "fallbacks": sorted({r["details"]["fallback_reason"] for r in rows if r["details"]["fallback_reason"]}),
        "median_latency_ms": sorted(r["latency_ms"] for r in rows)[len(rows) // 2] if rows else 0,
        "estimated_cost_usd": round(spent, 4) if live else 0.0,
    }
    out = ROOT / "eval" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    (out / f"{stamp}-{args.mode}.json").write_text(json.dumps({"summary": summary, "results": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    failed = [r for r in rows if not r["passed"]]
    for row in failed[:10]:
        print("FAIL", row["case_id"], {k: v for k, v in row["checks"].items() if not v}, row["details"].get("fallback_reason"))
    return 0 if not failed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["fake", "foundry"], default="fake")
    parser.add_argument("--limit", type=int, default=0, help="run an even sample of this many cases")
    parser.add_argument("--filter", default="", help="only cases whose id contains this text")
    parser.add_argument("--skip", type=int, default=0, help="skip this many cases from the start (to continue a stopped run)")
    parser.add_argument("--max-usd", type=float, default=0.0)
    parser.add_argument("--confirm-spend", action="store_true")
    parser.add_argument("--critic", action="store_true")
    sys.exit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
