"""Append-only JSONL audit trail with sensitive-data masking.

Masking happens BEFORE anything is written -- there is no "raw" log with
unmasked content sitting on disk anywhere.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b(\+?\d[\d\-. ]{7,}\d)\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SECRET_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|password|passwd|secret|token|ssn)\b\s*[:=]\s*\S+"
)


def mask(text: Any) -> Any:
    """Recursively mask sensitive-looking substrings in strings, dicts, lists."""
    if isinstance(text, dict):
        return {k: mask(v) for k, v in text.items()}
    if isinstance(text, (list, tuple)):
        return [mask(v) for v in text]
    if not isinstance(text, str):
        return text

    def _mask_email(m):
        local, domain = m.group(0).split("@", 1)
        return f"{local[0]}***@{domain}"

    out = _EMAIL_RE.sub(_mask_email, text)
    out = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}=***REDACTED***", out)
    out = _CARD_RE.sub(lambda m: "*" * (len(m.group(0)) - 4) + m.group(0)[-4:], out)
    out = _PHONE_RE.sub(lambda m: "***-***-" + re.sub(r"\D", "", m.group(0))[-4:], out)
    return out


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path or Path("audit_log.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, task_id: str, **fields: Any) -> None:
        entry = {
            "timestamp": time.time(),
            "event_type": event_type,
            "task_id": task_id,
            **mask(fields),
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "r") as f:
            return [json.loads(line) for line in f if line.strip()]
