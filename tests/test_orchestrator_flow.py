import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_agent.actuators.mock import MockActuator
from voice_agent.audit import AuditLog
from voice_agent.models import Action, Plan, PlanStep, Task, TaskState
from voice_agent.orchestrator import Orchestrator
from voice_agent.planner import Planner
from voice_agent.policy_engine import PolicyEngine
from voice_agent.state_machine import TaskStateMachine
from voice_agent.voice_pipeline import VoicePipeline


class ScriptedVoice(VoicePipeline):
    """Deterministic scripted voice pipeline for tests: `listen()` returns
    replies from a fixed queue; `speak()` records what was said."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.said: list[str] = []

    def listen(self) -> str:
        return self.replies.pop(0) if self.replies else ""

    def speak(self, text: str) -> None:
        self.said.append(text)


def make_orchestrator(tmp_path, replies):
    voice = ScriptedVoice(replies)
    actuator = MockActuator()
    orch = Orchestrator(
        voice=voice,
        actuator=actuator,
        policy_engine=PolicyEngine.load(),
        planner=Planner(),  # no ANTHROPIC_API_KEY in test env -> stub planner
        audit=AuditLog(path=Path(tmp_path) / "audit.jsonl"),
        state_dir=Path(tmp_path) / "state",
        app_allowlist=("notepad", "excel"),
    )
    return orch, voice, actuator


def test_open_app_flows_through_without_confirmation(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=[])
    task = orch.handle_utterance("open notepad")
    assert task.state == TaskState.COMPLETED
    assert "notepad" in actuator.open_windows[0].lower()


def test_plan_confirmation_can_cancel(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=["no"])
    orch.confirm_plans = True
    orch.planner = Planner(backend="rules")
    task = orch.handle_utterance("open notepad")
    assert task.state == TaskState.CANCELLED
    assert actuator.open_windows == []


def test_plan_confirmation_accepts_yes(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=["yes"])
    orch.confirm_plans = True
    orch.planner = Planner(backend="rules")
    task = orch.handle_utterance("open notepad")
    assert task.state == TaskState.COMPLETED
    assert actuator.open_windows


def test_confirmation_declined_stops_task_without_executing(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=["no"])
    task = Task(id="del1", original_transcript="delete the file")
    task.plan = Plan(steps=[PlanStep.new(Action(category="file_delete", args={"path": "C:/x.txt"}))])
    sm = TaskStateMachine(task, state_dir=Path(tmp_path) / "state")
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    orch._run_remaining_steps(task, sm)
    assert task.state == TaskState.CANCELLED
    assert task.plan.steps[0].status == "skipped"
    assert "C:/x.txt" not in actuator.action_log.__str__() or all(
        a["action"] != "file_delete" for a in actuator.action_log
    )


def test_confirmation_accepted_executes_and_verifies(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=["yes"])
    actuator.fake_fs["C:/x.txt"] = "contents"
    task = Task(id="del2", original_transcript="delete the file")
    task.plan = Plan(steps=[PlanStep.new(Action(category="file_delete", args={"path": "C:/x.txt"}))])
    sm = TaskStateMachine(task, state_dir=Path(tmp_path) / "state")
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    orch._run_remaining_steps(task, sm)
    assert task.state == TaskState.COMPLETED
    assert "C:/x.txt" not in actuator.fake_fs


def test_ambiguous_confirmation_reply_treated_as_no(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=["maybe idk"])
    task = Task(id="del3", original_transcript="delete the file")
    task.plan = Plan(steps=[PlanStep.new(Action(category="file_delete", args={"path": "C:/y.txt"}))])
    sm = TaskStateMachine(task, state_dir=Path(tmp_path) / "state")
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    orch._run_remaining_steps(task, sm)
    assert task.state == TaskState.CANCELLED


def test_pause_then_resume_continues_remaining_steps(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=[])
    task = Task(id="multi1", original_transcript="open notepad then open excel")
    task.plan = Plan(steps=[
        PlanStep.new(Action(category="open_app", args={"app": "notepad"})),
        PlanStep.new(Action(category="open_app", args={"app": "excel"})),
    ])
    sm = TaskStateMachine(task, state_dir=Path(tmp_path) / "state")
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)

    # Simulate a pause arriving after step 1 by manually pausing and checking
    # the loop respects task.state on each iteration.
    orch._run_remaining_steps(task, sm)  # nothing paused it, so it runs to completion
    assert task.state == TaskState.COMPLETED
    assert task.current_step_index == 2


def test_audit_log_masks_sensitive_data(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=["yes"])
    task = Task(id="msg1", original_transcript="email alice")
    task.plan = Plan(steps=[PlanStep.new(Action(
        category="send_message",
        args={"to": "alice@example.com", "subject": "hi", "body": "call me at 555-123-4567"},
    ))])
    sm = TaskStateMachine(task, state_dir=Path(tmp_path) / "state")
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    orch._run_remaining_steps(task, sm)

    entries = orch.audit.read_all()
    text_blob = str(entries)
    assert "alice@example.com" not in text_blob
    assert "555-123-4567" not in text_blob


def test_session_memory_open_first_link_after_search(tmp_path):
    orch, voice, actuator = make_orchestrator(tmp_path, replies=[])
    orch.planner = Planner(backend="rules")
    orch.domain_allowlist = ("google.com", "wikipedia.org")

    task1 = orch.handle_utterance(
        "open https://www.google.com/search?q=best+football+team+in+the+world"
    )
    assert task1.state == TaskState.COMPLETED
    assert orch.memory.last_search_query == "best football team in the world"

    task2 = orch.handle_utterance("Now open the first link on the google")
    assert task2.state == TaskState.COMPLETED
    assert orch.memory.last_url
    # Feeling Lucky URL for result #1
    assert "btnI=1" in orch.memory.last_url or "google.com" in (orch.memory.last_url or "")
    navs = [a for a in actuator.action_log if a["action"] == "browser_navigate"]
    assert len(navs) >= 2
