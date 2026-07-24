"""Deterministic policy engine.

This module is the ONLY authority that may grant permission for an action.
It is intentionally free of any LLM call, any network call, and any
randomness: `evaluate()` is a pure function of (Action, PolicyContext, rule
table). Same inputs -> same output, always. That property is what lets us
unit-test "the agent can never be talked into X" as a fact about code rather
than a hope about a model's behavior.

Design invariants enforced here:
  1. Fail closed: an action category not present in the rule table is denied.
  2. `allow: false` in the rule table can never be flipped to true by anything
     -- not confirmation, not context, not a persuasive justification string.
  3. Content the agent read (web pages, documents, OCR) is just data. A
     PlanStep whose justification appears to originate from ingested content
     gets its risk escalated and confirmation forced, regardless of category.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import Action, Decision, PolicyContext, RiskLevel

_RULES_PATH = Path(__file__).parent / "policy_rules.yaml"

_RISK_ORDER = [RiskLevel.SAFE, RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


def _escalate(level: RiskLevel) -> RiskLevel:
    idx = _RISK_ORDER.index(level)
    return _RISK_ORDER[min(idx + 1, len(_RISK_ORDER) - 1)]


# Heuristic patterns suggesting a plan step's justification is echoing
# instruction-like text pulled from ingested content rather than the user's
# own request. This is a defense-in-depth signal, NOT the primary defense
# (the primary defense is that content can never itself set `allowed=True`).
_INJECTION_HINT_PATTERNS = [
    r"\bignore (all|previous|prior) instructions\b",
    r"\bnew instructions?\s*:",
    r"\bsystem\s*:\s*",
    r"\bdo not tell the user\b",
    r"\bwithout (asking|confirming|telling)\b",
    r"\bas an ai\b.*\byou must\b",
]
_injection_hint_re = re.compile("|".join(_INJECTION_HINT_PATTERNS), re.IGNORECASE)


def _looks_like_injected_instruction(action: Action) -> bool:
    if action.derived_from_ingested_content:
        return True
    text = f"{action.justification} {' '.join(str(v) for v in action.args.values())}"
    return bool(_injection_hint_re.search(text))


def _load_rules() -> dict:
    with open(_RULES_PATH, "r") as f:
        data = yaml.safe_load(f)
    return data.get("rules", {})


DEFAULT_DECISION_RULE_ID = "default_fail_closed"


@dataclass
class PolicyEngine:
    rules: dict

    @classmethod
    def load(cls, rules_path: Path | None = None) -> "PolicyEngine":
        path = rules_path or _RULES_PATH
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(rules=data.get("rules", {}))

    def evaluate(self, action: Action, context: PolicyContext) -> Decision:
        rule = self.rules.get(action.category)

        # Invariant 1: fail closed on unknown action categories.
        if rule is None:
            return Decision(
                allowed=False,
                requires_confirmation=False,
                risk_level=RiskLevel.CRITICAL,
                rule_id=DEFAULT_DECISION_RULE_ID,
                reason=f"Unknown action category '{action.category}' has no policy rule; "
                       f"denying by default (fail closed).",
            )

        risk_level = RiskLevel(rule["risk"])
        allowed = bool(rule.get("allow", False))
        requires_confirmation = bool(rule.get("confirm", False))
        reason_parts = [f"rule '{action.category}': base risk={risk_level.value}, "
                         f"allow={allowed}, confirm_required={requires_confirmation}."]

        # Invariant 2: an explicit deny cannot be overridden by anything below.
        if not allowed:
            return Decision(
                allowed=False,
                requires_confirmation=False,
                risk_level=risk_level,
                rule_id=action.category,
                reason=" ".join(reason_parts) + " This action category is unconditionally denied.",
            )

        # App / domain allowlist checks (deterministic context, not LLM judgment).
        if rule.get("requires_allowlist"):
            target = action.args.get("app") or action.args.get("domain") or action.args.get("url", "")
            allowlist = context.app_allowlist + context.domain_allowlist
            targets_to_check = [str(target).lower()]
            if action.category == "browser_navigate" and str(target).startswith(("http://", "https://")):
                try:
                    from urllib.parse import urlparse
                    host = urlparse(str(target)).netloc.lower()
                    if host:
                        targets_to_check.append(host)
                        if host.startswith("www."):
                            targets_to_check.append(host[4:])
                except Exception:  # noqa: BLE001
                    pass
            if allowlist and not any(
                any(entry.lower() in candidate for entry in allowlist)
                for candidate in targets_to_check
            ):
                return Decision(
                    allowed=False,
                    requires_confirmation=False,
                    risk_level=_escalate(risk_level),
                    rule_id=f"{action.category}:allowlist_violation",
                    reason=f"Target '{target}' is not on the configured allowlist {allowlist}.",
                )

        # Rate limiting for destructive-but-allowed categories.
        if rule.get("rate_limited") and action.category == "file_delete":
            if context.deletes_this_task >= context.max_deletes_per_task:
                return Decision(
                    allowed=False,
                    requires_confirmation=False,
                    risk_level=_escalate(risk_level),
                    rule_id=f"{action.category}:rate_limit_exceeded",
                    reason=f"Delete rate limit ({context.max_deletes_per_task} per task) exceeded.",
                )

        # Do-not-disturb window: escalate anything above LOW to require confirmation.
        if context.do_not_disturb and _RISK_ORDER.index(risk_level) > _RISK_ORDER.index(RiskLevel.LOW):
            requires_confirmation = True
            reason_parts.append("Do-not-disturb window active; forcing confirmation.")

        # Invariant 3: injected-instruction heuristic escalates risk + forces confirmation.
        if _looks_like_injected_instruction(action):
            risk_level = _escalate(risk_level)
            requires_confirmation = True
            reason_parts.append(
                "Action justification/args appear derived from ingested content "
                "(web page / document / OCR) or contain injection-like phrasing; "
                "escalating risk and forcing confirmation regardless of category default."
            )

        return Decision(
            allowed=True,
            requires_confirmation=requires_confirmation,
            risk_level=risk_level,
            rule_id=action.category,
            reason=" ".join(reason_parts),
        )

    def fields_to_disclose(self, action: Action) -> list[str]:
        """Which argument names must be shown verbatim in a confirmation prompt."""
        rule = self.rules.get(action.category, {})
        return list(rule.get("show", []))
