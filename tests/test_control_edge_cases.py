"""Regression tests: control commands (pause/resume/cancel/correction) issued
in a state where they don't apply must degrade gracefully (a spoken message)
rather than raising an exception up through the orchestrator.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_agent.actuators.mock import MockActuator
from voice_agent.audit import AuditLog
from voice_agent.models import Task, TaskState
from voice_agent.orchestrator import Orchestrator
from voice_agent.planner import Planner
from voice_agent.policy_engine import PolicyEngine
from voice_agent.state_machine import TaskStateMachine
from voice_agent.voice_pipeline import VoicePipeline


class RecordingVoice(VoicePipeline):
    def __init__(self):
        self.said = []

    def listen(self):
        return ""

    def speak(self, text):
        self.said.append(text)


def make_orchestrator(tmp_path):
    voice = RecordingVoice()
    orch = Orchestrator(
        voice=voice, actuator=MockActuator(), policy_engine=PolicyEngine.load(),
        planner=Planner(), audit=AuditLog(path=Path(tmp_path) / "a.jsonl"),
        state_dir=Path(tmp_path) / "state",
    )
    return orch, voice


def test_resume_with_nothing_paused_does_not_raise(tmp_path):
    orch, voice = make_orchestrator(tmp_path)
    task = Task(id="r1", original_transcript="x")
    sm = TaskStateMachine(task, state_dir=Path(tmp_path) / "state")
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.WAITING_CLARIFICATION)
    result = orch.handle_utterance("resume", task=task)
    assert result.state == TaskState.WAITING_CLARIFICATION
    assert any("nothing is paused" in s.lower() for s in voice.said)


def test_pause_with_nothing_running_does_not_raise(tmp_path):
    orch, voice = make_orchestrator(tmp_path)
    task = Task(id="p1", original_transcript="x")  # state CREATED
    result = orch.handle_utterance("pause", task=task)
    assert result.state == TaskState.CREATED
    assert any("nothing currently running" in s.lower() for s in voice.said)


def test_cancel_on_completed_task_does_not_raise(tmp_path):
    orch, voice = make_orchestrator(tmp_path)
    task = Task(id="c1", original_transcript="x")
    sm = TaskStateMachine(task, state_dir=Path(tmp_path) / "state")
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    sm.transition(TaskState.COMPLETED)
    result = orch.handle_utterance("cancel", task=task)
    assert result.state == TaskState.COMPLETED
    assert any("nothing active to cancel" in s.lower() for s in voice.said)
