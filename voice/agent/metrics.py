"""
agent/metrics.py
Aggregate metrics from audit JSONL logs for /metrics and diagnostics.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

AUDIT_DIR = Path(__file__).parent.parent / "audit_logs"


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def load_sessions(limit: int | None = None) -> list[list[dict]]:
    """Load audit log files as list of sessions (each session = list of entries)."""
    files = sorted(AUDIT_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if limit:
        files = files[:limit]
    sessions = []
    for path in files:
        entries = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
        if entries:
            sessions.append(entries)
    return sessions


def compute_metrics(max_sessions: int = 500) -> dict:
    """Compute aggregate metrics from recent audit logs."""
    sessions = load_sessions(limit=max_sessions)

    outcomes = Counter()
    tool_calls = Counter()
    tool_failures = Counter()
    errors: list[str] = []
    durations_plan: list[float] = []
    durations_tool: list[float] = []
    durations_transcribe: list[float] = []

    for entries in sessions:
        outcome = "unknown"
        for e in entries:
            t = e.get("type")
            if t == "outcome":
                outcome = e.get("status", "unknown")
            elif t == "action":
                tool = e.get("tool", "?")
                tool_calls[tool] += 1
                if not e.get("verified") or not e.get("result", {}).get("success", True):
                    tool_failures[tool] += 1
                if "duration_ms" in e:
                    durations_tool.append(e["duration_ms"])
            elif t == "plan" and "duration_ms" in e:
                durations_plan.append(e["duration_ms"])
            elif t == "transcript" and "duration_ms" in e:
                durations_transcribe.append(e["duration_ms"])
            elif t == "error":
                errors.append(e.get("message", ""))
        outcomes[outcome] += 1

    total = len(sessions) or 1
    success = outcomes.get("success", 0)
    partial = outcomes.get("partial", 0)
    failed = total - success - partial

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "sessions_analyzed": len(sessions),
        "success_rate": round(success / total * 100, 1),
        "outcomes": dict(outcomes),
        "avg_transcribe_ms": _avg(durations_transcribe),
        "avg_plan_ms": _avg(durations_plan),
        "avg_tool_ms": _avg(durations_tool),
        "tool_calls": dict(tool_calls.most_common(20)),
        "tool_failures": dict(tool_failures.most_common(10)),
        "recent_errors": errors[:10],
    }


def group_failures(max_sessions: int = 500) -> list[dict]:
    """Group recurring errors from audit logs (Comet Diagnostics-style)."""
    sessions = load_sessions(limit=max_sessions)
    groups: dict[str, dict] = {}

    for entries in sessions:
        sid = entries[0].get("ts", "?")[:10] if entries else "?"
        for e in entries:
            if e.get("type") != "error":
                continue
            msg = (e.get("message") or "").strip()
            if not msg:
                continue
            key = msg[:120]
            if key not in groups:
                groups[key] = {"message": msg, "count": 0, "sessions": []}
            groups[key]["count"] += 1
            if len(groups[key]["sessions"]) < 5:
                groups[key]["sessions"].append(sid)

    ranked = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
    return ranked[:25]
