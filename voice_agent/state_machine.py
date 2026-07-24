"""Task state machine.

Explicit transition table so illegal transitions (e.g. resuming a cancelled
task) raise instead of silently happening. State is snapshotted to disk after
every transition so a task can be paused/resumed across process restarts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .models import Task, TaskState

ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {TaskState.PLANNING, TaskState.CANCELLED},
    TaskState.PLANNING: {TaskState.WAITING_CLARIFICATION, TaskState.AWAITING_CONFIRMATION,
                          TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.WAITING_CLARIFICATION: {TaskState.PLANNING, TaskState.CANCELLED},
    TaskState.AWAITING_CONFIRMATION: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.PLANNING},
    TaskState.RUNNING: {TaskState.PAUSED, TaskState.AWAITING_CONFIRMATION, TaskState.COMPLETED,
                         TaskState.CANCELLED, TaskState.FAILED, TaskState.PLANNING},
    TaskState.PAUSED: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.PLANNING},
    TaskState.COMPLETED: set(),
    TaskState.CANCELLED: set(),
    TaskState.FAILED: {TaskState.PLANNING},  # allow retry-with-revised-plan
}


class IllegalTransitionError(Exception):
    pass


class TaskStateMachine:
    def __init__(self, task: Task, state_dir: Path | None = None):
        self.task = task
        self.state_dir = state_dir or Path("state")
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def can_transition(self, new_state: TaskState) -> bool:
        return new_state in ALLOWED_TRANSITIONS.get(self.task.state, set())

    def transition(self, new_state: TaskState) -> None:
        if not self.can_transition(new_state):
            raise IllegalTransitionError(
                f"Cannot transition task {self.task.id} from {self.task.state.value} "
                f"to {new_state.value}. Allowed: "
                f"{[s.value for s in ALLOWED_TRANSITIONS.get(self.task.state, set())]}"
            )
        self.task.state = new_state
        self.task.updated_at = time.time()
        self._snapshot()

    def _snapshot_path(self) -> Path:
        return self.state_dir / f"{self.task.id}.json"

    def _snapshot(self) -> None:
        with open(self._snapshot_path(), "w") as f:
            json.dump(self.task.to_dict(), f, indent=2)

    @classmethod
    def load_snapshot(cls, task_id: str, state_dir: Path | None = None) -> dict | None:
        state_dir = state_dir or Path("state")
        path = state_dir / f"{task_id}.json"
        if not path.exists():
            return None
        with open(path, "r") as f:
            return json.load(f)
