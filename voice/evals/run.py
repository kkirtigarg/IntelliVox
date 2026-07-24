#!/usr/bin/env python3
"""
evals/run.py — Run planner evals against voice_commands.jsonl (Opik/Comet-style test suite).

Usage (from voice/):
  python -m evals.run
  python -m evals.run --limit 5
  python -m evals.run --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

EVALS_FILE = Path(__file__).parent / "voice_commands.jsonl"


def load_cases(path: Path) -> list[dict]:
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def tools_match(plan: dict, expected: list[str]) -> bool:
    if not expected:
        return True
    actual = [s.get("tool") for s in plan.get("steps", [])]
    return actual == expected


def run_eval(limit: int | None, verbose: bool) -> int:
    from agent.planner import plan

    cases = load_cases(EVALS_FILE)
    if limit:
        cases = cases[:limit]

    passed = 0
    failed = 0
    results = []

    print(f"Running {len(cases)} planner evals from {EVALS_FILE.name}\n")

    for i, case in enumerate(cases, 1):
        inp = case["input"]
        expected_tools = case.get("expected_tools", [])
        allow_clarify = case.get("allow_clarify", False)

        t0 = time.perf_counter()
        try:
            action_plan = plan(inp)
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as e:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            action_plan = {"error": str(e)}
            failed += 1
            status = "ERROR"
            results.append({"input": inp, "status": status, "error": str(e)})
            print(f"  [{i}] FAIL  ERROR  {inp!r}  ({latency_ms}ms)")
            continue

        actual_tools = [s.get("tool") for s in action_plan.get("steps", [])]
        clarify = action_plan.get("clarification_needed", False)

        ok = True
        if action_plan.get("error"):
            ok = False
        elif clarify and not allow_clarify and expected_tools:
            ok = False
        elif expected_tools and not tools_match(action_plan, expected_tools):
            ok = False
        elif not expected_tools and not clarify and action_plan.get("steps") and allow_clarify:
            ok = True  # clarify-only case

        if ok:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        row = {
            "input": inp,
            "status": status,
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "latency_ms": latency_ms,
            "intent": action_plan.get("intent"),
        }
        results.append(row)

        mark = "✓" if ok else "✗"
        print(f"  [{i}] {mark} {status:5}  {inp!r}")
        if verbose or not ok:
            print(f"         expected: {expected_tools}")
            print(f"         actual:   {actual_tools}  ({latency_ms}ms)")

    total = passed + failed
    rate = round(passed / total * 100, 1) if total else 0
    print(f"\n{'─' * 50}")
    print(f"Results: {passed}/{total} passed ({rate}%)")

    report_path = Path(__file__).parent / "last_run.json"
    report_path.write_text(json.dumps({"passed": passed, "failed": failed, "rate": rate, "results": results}, indent=2))
    print(f"Report:  {report_path}")

    return 0 if failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="IntelliVox planner eval suite")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    sys.exit(run_eval(args.limit, args.verbose))


if __name__ == "__main__":
    main()
