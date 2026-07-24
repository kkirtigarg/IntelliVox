"""
src/memory/session_memory.py
Active application/window/file/document state for one session.
"""
from .models import SessionState


class SessionMemory:
    def __init__(self):
        self.state = SessionState()

    def apply(self, **fields) -> None:
        """Set one or more SessionState fields, e.g. apply(active_application='Chrome')."""
        for key, value in fields.items():
            setattr(self.state, key, value)

    def mark_completed(self, task: str) -> None:
        self.state.completed_tasks.append(task)
