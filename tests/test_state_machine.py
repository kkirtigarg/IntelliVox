import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_agent.models import Task, TaskState
from voice_agent.state_machine import TaskStateMachine, IllegalTransitionError


def make_sm(tmp_path):
    task = Task(id="t1", original_transcript="do something")
    return TaskStateMachine(task, state_dir=Path(tmp_path)), task


def test_legal_transition_sequence(tmp_path):
    sm, task = make_sm(tmp_path)
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    sm.transition(TaskState.PAUSED)
    sm.transition(TaskState.RUNNING)
    sm.transition(TaskState.COMPLETED)
    assert task.state == TaskState.COMPLETED


def test_illegal_transition_raises(tmp_path):
    sm, task = make_sm(tmp_path)
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    sm.transition(TaskState.CANCELLED)
    try:
        sm.transition(TaskState.RUNNING)
        assert False, "expected IllegalTransitionError"
    except IllegalTransitionError:
        pass


def test_snapshot_persisted_and_reloadable(tmp_path):
    sm, task = make_sm(tmp_path)
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    snap = TaskStateMachine.load_snapshot("t1", state_dir=Path(tmp_path))
    assert snap is not None
    assert snap["state"] == "RUNNING"


def test_completed_and_cancelled_are_terminal(tmp_path):
    sm, task = make_sm(tmp_path)
    sm.transition(TaskState.PLANNING)
    sm.transition(TaskState.RUNNING)
    sm.transition(TaskState.COMPLETED)
    assert sm.can_transition(TaskState.RUNNING) is False
    assert sm.can_transition(TaskState.PAUSED) is False
