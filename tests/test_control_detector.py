import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_agent.control_detector import detect
from voice_agent.models import ControlCommand


def test_pause_variants():
    for phrase in ["pause", "hold on a sec", "wait a moment please", "hang on"]:
        assert detect(phrase) == ControlCommand.PAUSE, phrase


def test_resume_variants():
    for phrase in ["resume", "continue please", "keep going", "go ahead"]:
        assert detect(phrase) == ControlCommand.RESUME, phrase


def test_cancel_variants():
    for phrase in ["cancel", "abort this", "never mind", "stop"]:
        assert detect(phrase) == ControlCommand.CANCEL, phrase


def test_correction_variants():
    for phrase in ["undo that", "actually send it to bob instead", "no wait", "i meant the other file"]:
        assert detect(phrase) == ControlCommand.CORRECTION, phrase


def test_ordinary_task_language_does_not_trigger_control():
    for phrase in [
        "open excel and load the quarterly report",
        "email the report to finance",
        "navigate to the company wiki",
    ]:
        assert detect(phrase) == ControlCommand.NONE, phrase


def test_determinism():
    assert detect("please pause") == detect("please pause")


def test_cancel_takes_priority_over_correction_words():
    # "no wait, cancel that" should resolve to CANCEL, not CORRECTION
    assert detect("no wait, cancel that") == ControlCommand.CANCEL
