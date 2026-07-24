"""
agent/audit.py
Structured audit trail — logs every transcript, decision, action, and outcome.
Sensitive data (passwords, tokens) is masked.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("intellivox.audit")

AUDIT_DIR  = Path(__file__).parent.parent / "audit_logs"
AUDIT_DIR.mkdir(exist_ok=True)

SENSITIVE_PATTERNS = [
    r"password\s*[:=]\s*\S+",
    r"token\s*[:=]\s*\S+",
    r"api[_-]?key\s*[:=]\s*\S+",
    r"secret\s*[:=]\s*\S+",
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # email
]

def _mask(text: str) -> str:
    for pat in SENSITIVE_PATTERNS:
        text = re.sub(pat, "[REDACTED]", text, flags=re.IGNORECASE)
    return text


class AuditSession:
    """Tracks one user interaction from transcript to final outcome."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.entries: list[dict] = []
        self._file = AUDIT_DIR / f"{session_id}.jsonl"

    def _write(self, entry: dict):
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        self.entries.append(entry)
        with open(self._file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        log.debug("AUDIT %s", entry)

    def log_transcript(self, text: str, language: str, duration_ms: float | None = None):
        entry = {"type": "transcript", "text": _mask(text), "language": language}
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 1)
        self._write(entry)

    def log_plan(self, plan: dict, duration_ms: float | None = None):
        safe_plan = json.loads(_mask(json.dumps(plan)))
        entry = {"type": "plan", "plan": safe_plan}
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 1)
        self._write(entry)

    def log_safety(self, tool: str, args: dict, decision: str, reason: str):
        self._write({
            "type":     "safety",
            "tool":     tool,
            "args":     json.loads(_mask(json.dumps(args))),
            "decision": decision,
            "reason":   reason,
        })

    def log_action(
        self,
        tool: str,
        args: dict,
        result: dict,
        verified: bool,
        step_index: int | None = None,
        duration_ms: float | None = None,
    ):
        entry = {
            "type":     "action",
            "tool":     tool,
            "args":     json.loads(_mask(json.dumps(args))),
            "result":   result,
            "verified": verified,
        }
        if step_index is not None:
            entry["step_index"] = step_index
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 1)
        self._write(entry)

    def log_confirmation(self, tool: str, user_response: str):
        self._write({"type": "confirmation", "tool": tool, "response": user_response})

    def log_error(self, message: str):
        self._write({"type": "error", "message": _mask(message)})

    def log_outcome(self, status: str, summary: str):
        self._write({"type": "outcome", "status": status, "summary": _mask(summary)})

    def summary(self) -> dict:
        return {
            "session_id":  self.session_id,
            "started_at":  self.started_at,
            "entry_count": len(self.entries),
            "file":        str(self._file),
        }
