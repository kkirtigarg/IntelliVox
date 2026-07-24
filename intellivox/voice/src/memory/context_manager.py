"""
src/memory/context_manager.py
Facade tying Session/Browser/History/Conversation memory together for one
WebSocket session. agent/orchestrator.py talks only to this class.
"""
from __future__ import annotations

from src.connectors import executor_memory_connector

from .browser_memory import BrowserMemory
from .conversation_memory import ConversationMemory
from .history_memory import HistoryMemory
from .session_memory import SessionMemory


def _clarify(question: str) -> dict:
    return {
        "intent": "clarify",
        "explanation": "",
        "steps": [],
        "clarification_needed": True,
        "clarification_question": question,
    }


def _plan_from_steps(steps: list[dict], intent: str, reset_after: bool = False) -> dict:
    result = {
        "intent": intent,
        "explanation": f"Memory-driven action: {intent}",
        "steps": steps,
        "clarification_needed": False,
        "clarification_question": None,
    }
    if reset_after:
        result["_reset_after"] = True
    return result


def _single_step(tool: str, args: dict, intent: str) -> dict:
    return _plan_from_steps([{"tool": tool, "args": args}], intent=intent)


def _close_steps_for_active_apps(state) -> list[dict]:
    names = {n for n in (state.active_application, state.active_browser) if n}
    return [{"tool": "close_app", "args": {"name": n}} for n in names]


class ContextManager:
    """Owns all memory for one session (one WebSocket connection)."""

    META_GO_BACK = {"go back", "go back a page", "back", "go back a step", "previous page"}
    META_REPEAT = {
        "repeat", "repeat that", "repeat previous step", "repeat the previous step",
        "do that again", "do it again",
    }
    META_RESUME = {"resume", "resume workflow", "resume task"}
    META_CONTINUE = {"continue", "keep going", "carry on"}
    META_RESET = {"close everything", "exit", "reset session", "reset", "clear everything", "clear session"}

    def __init__(self):
        self.session = SessionMemory()
        self.browser = BrowserMemory()
        self.history = HistoryMemory()
        self.conversation = ConversationMemory()
        self._last_command_raw: str | None = None

    def note_command(self, transcript: str) -> None:
        """Log every command spoken, regardless of outcome (transparency log)."""
        self._last_command_raw = transcript
        self.conversation.record(transcript)

    def handle_meta_command(self, transcript: str) -> dict | None:
        """
        Returns a plan-shaped dict (so it drops into the existing execution
        loop unchanged) for go back / repeat / resume / continue / reset.
        Returns None when the transcript isn't a meta-command, meaning the
        caller should fall through to the normal LLM planner.
        """
        t = transcript.lower().strip().rstrip(".!?")

        if t in self.META_GO_BACK:
            url = self.browser.pop_previous_url()
            if not url:
                return _clarify("There's no previous page in memory to go back to.")
            return _single_step("navigate_url", {"url": url}, intent="go_back")

        if t in self.META_REPEAT:
            last = self.history.last()
            if not last:
                return _clarify("There's nothing recorded yet to repeat.")
            return _single_step(last.tool, dict(last.args), intent="repeat")

        if t in self.META_RESUME:
            return {"_control": "resume"}

        if t in self.META_CONTINUE:
            pending = self.session.state.pending_tasks
            if not pending:
                return _clarify("There's nothing paused to continue.")
            self.session.state.pending_tasks = []
            return _plan_from_steps(pending, intent="continue")

        if t in self.META_RESET:
            steps = _close_steps_for_active_apps(self.session.state)
            return _plan_from_steps(steps, intent="reset", reset_after=True)

        return None

    def record_success(self, tool: str, args: dict, result: dict) -> None:
        """Called only after a step succeeded AND was verified."""
        self.history.record(tool, args, result)
        executor_memory_connector.apply(self.session, self.browser, tool, args, result)
        self.session.apply(previous_action=tool, previous_command=self._last_command_raw)

    def save_pending(self, steps: list[dict]) -> None:
        self.session.state.pending_tasks = steps

    def reset(self) -> None:
        self.session = SessionMemory()
        self.browser = BrowserMemory()
        self.history = HistoryMemory()
        self.conversation = ConversationMemory()
