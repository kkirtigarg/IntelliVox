"""
agent/plan_validator.py
Validate planner output against the real tool registry.
"""
from __future__ import annotations

import logging

from agent.tools import TOOLS

log = logging.getLogger("intellivox.planner")


def validate_plan(action_plan: dict) -> dict:
    """
    Drop unknown tools; return clarification if nothing valid remains.
    """
    steps = action_plan.get("steps") or []
    valid: list[dict] = []
    invalid: list[str] = []

    for step in steps:
        tool = (step.get("tool") or "").strip()
        if tool in TOOLS:
            valid.append(step)
        elif tool:
            invalid.append(tool)

    if invalid:
        log.warning("Plan validation dropped unknown tools: %s", invalid)

    if not valid and steps:
        return {
            "intent": action_plan.get("intent", "invalid_plan"),
            "explanation": "Could not build a valid plan with available tools.",
            "steps": [],
            "clarification_needed": True,
            "clarification_question": (
                f"I can't run these tools: {', '.join(sorted(set(invalid)))}. "
                "Please rephrase using supported actions."
            ),
        }

    out = dict(action_plan)
    out["steps"] = valid
    return out
