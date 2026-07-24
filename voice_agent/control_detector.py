"""Deterministic control-command detection.

Pause, resume, cancel, and correction are safety-relevant control operations
(the user must always be able to stop the agent). They must not depend on an
LLM call succeeding, being fast, or interpreting things correctly, so this is
a plain keyword/regex matcher: same transcript text -> same control command,
every time, with no network dependency.

This runs on every transcript BEFORE anything is sent to the planner.
"""
from __future__ import annotations

import re

from .models import ControlCommand

_PAUSE_PATTERNS = [
    r"\bpause\b", r"\bhold on\b", r"\bwait a (sec|second|moment)\b", r"\bhang on\b",
]
_RESUME_PATTERNS = [
    r"\bresume\b", r"\bcontinue\b", r"\bkeep going\b", r"\bgo ahead\b", r"\bproceed\b",
]
_CANCEL_PATTERNS = [
    r"\bcancel\b", r"\babort\b", r"\bstop that\b", r"\bnever mind\b", r"\bnevermind\b",
    r"\bforget it\b", r"^stop$",
]
_CORRECTION_PATTERNS = [
    r"\bundo\b", r"\bactually\b.*\binstead\b", r"\bno,? wait\b", r"\bthat'?s wrong\b",
    r"\bi meant\b", r"\bnot that\b",
]

_compiled = {
    ControlCommand.PAUSE: re.compile("|".join(_PAUSE_PATTERNS), re.IGNORECASE),
    ControlCommand.RESUME: re.compile("|".join(_RESUME_PATTERNS), re.IGNORECASE),
    ControlCommand.CANCEL: re.compile("|".join(_CANCEL_PATTERNS), re.IGNORECASE),
    ControlCommand.CORRECTION: re.compile("|".join(_CORRECTION_PATTERNS), re.IGNORECASE),
}

# Order matters: cancel/pause/correction are checked before resume so that,
# e.g., "no wait, cancel that" resolves to CANCEL not CORRECTION+RESUME.
_CHECK_ORDER = [ControlCommand.CANCEL, ControlCommand.PAUSE, ControlCommand.CORRECTION, ControlCommand.RESUME]


def detect(transcript: str) -> ControlCommand:
    """Pure function: same transcript text -> same ControlCommand, always."""
    text = transcript.strip()
    if not text:
        return ControlCommand.NONE
    for cmd in _CHECK_ORDER:
        if _compiled[cmd].search(text):
            return cmd
    return ControlCommand.NONE
