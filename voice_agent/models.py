"""Shared data models for the voice-controlled computer-use agent.

Kept deliberately dependency-free (stdlib only) so policy/state logic can be
unit tested without pulling in ASR/TTS/GUI libraries.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    def __lt__(self, other: "RiskLevel") -> bool:
        order = [RiskLevel.SAFE, RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        return order.index(self) < order.index(other)


class ControlCommand(str, Enum):
    NONE = "NONE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CANCEL = "CANCEL"
    CORRECTION = "CORRECTION"


class TaskState(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass
class Action:
    """A single concrete, executable action proposed by the planner."""
    category: str  # e.g. "open_app", "gui_click", "file_delete", "send_message"
    args: dict = field(default_factory=dict)
    justification: str = ""
    source_step_id: str = ""
    # True if this action's justification/args are derived from content the
    # agent read (web page, document, OCR) rather than the user's own speech.
    derived_from_ingested_content: bool = False


@dataclass
class PolicyContext:
    """Deterministic context the policy engine is allowed to consider."""
    app_allowlist: tuple = ()
    domain_allowlist: tuple = ()
    deletes_this_task: int = 0
    max_deletes_per_task: int = 3
    do_not_disturb: bool = False
    now_epoch: float = field(default_factory=time.time)


@dataclass
class Decision:
    allowed: bool
    requires_confirmation: bool
    risk_level: RiskLevel
    rule_id: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "risk_level": self.risk_level.value,
            "rule_id": self.rule_id,
            "reason": self.reason,
        }


@dataclass
class PlanStep:
    id: str
    action: Action
    status: str = "pending"  # pending | confirmed | running | done | failed | skipped

    @staticmethod
    def new(action: Action) -> "PlanStep":
        return PlanStep(id=str(uuid.uuid4())[:8], action=action)


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    clarification_question: Optional[str] = None

    @property
    def needs_clarification(self) -> bool:
        return self.clarification_question is not None


@dataclass
class Task:
    id: str
    original_transcript: str
    state: TaskState = TaskState.CREATED
    plan: Plan = field(default_factory=Plan)
    current_step_index: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_transcript": self.original_transcript,
            "state": self.state.value,
            "current_step_index": self.current_step_index,
            "steps": [
                {
                    "id": s.id,
                    "category": s.action.category,
                    "args": s.action.args,
                    "status": s.status,
                }
                for s in self.plan.steps
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
