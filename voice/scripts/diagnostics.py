#!/usr/bin/env python3
"""
scripts/diagnostics.py — Group recurring failures from audit logs.

Usage (from voice/):
  python scripts/diagnostics.py
  python scripts/diagnostics.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.metrics import compute_metrics, group_failures


def main():
    parser = argparse.ArgumentParser(description="IntelliVox failure diagnostics")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    metrics = compute_metrics()
    failures = group_failures()

    if args.json:
        print(json.dumps({"metrics": metrics, "failures": failures}, indent=2))
        return

    print("IntelliVox Diagnostics")
    print("=" * 50)
    print(f"Sessions analyzed:  {metrics['sessions_analyzed']}")
    print(f"Success rate:       {metrics['success_rate']}%")
    print(f"Outcomes:           {metrics['outcomes']}")
    if metrics.get("avg_transcribe_ms"):
        print(f"Avg transcribe:     {metrics['avg_transcribe_ms']} ms")
    if metrics.get("avg_plan_ms"):
        print(f"Avg plan:           {metrics['avg_plan_ms']} ms")
    if metrics.get("avg_tool_ms"):
        print(f"Avg tool:           {metrics['avg_tool_ms']} ms")
    print()
    print("Top recurring failures:")
    print("-" * 50)
    if not failures:
        print("  (none)")
    for i, g in enumerate(failures, 1):
        print(f"  {i}. [{g['count']}x] {g['message'][:100]}")


if __name__ == "__main__":
    main()
