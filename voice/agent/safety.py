"""
agent/safety.py
Deterministic, rule-based safety guardrails.
NO LLM is involved in safety decisions — same input always gives same output.
"""
from enum import Enum
from typing import NamedTuple

class Decision(str, Enum):
    ALLOW   = "allow"    # execute immediately
    CONFIRM = "confirm"  # ask user first
    BLOCK   = "block"    # refuse entirely

class SafetyResult(NamedTuple):
    decision:    Decision
    reason:      str
    risk_level:  str  # low / medium / high / critical


# ── Tool classification ────────────────────────────────────────────────────────

# Tools that are always safe — no confirmation needed
SAFE_TOOLS = {
    "open_browser", "navigate_url", "google_search", "youtube_search", "youtube_play",
    "open_app", "take_screenshot", "list_files", "read_file",
    "press_key", "set_volume", "open_file", "find_file",
    "read_pdf", "summarize", "summarize_codebase", "save_summary_file", "answer_question",
    "search_mail", "read_mail", "open_gmail", "read_gmail",
}

# Tools that need explicit user confirmation before running
CONFIRM_TOOLS = {
    "write_file":   "This will write/overwrite a file on your computer.",
    "move_file":    "This will move or rename a file.",
    "close_app":    "This will close an application.",
    "type_text":    "This will type text into the active window.",
    "click":        "This will click somewhere on your screen.",
    "computer_use": "This will control your mouse and keyboard autonomously to complete the task — like a human using the computer.",
    "web_browse":   "This will open a web browser (Playwright) and load pages to complete the task.",
    "delete_file":  "⚠ This will permanently delete a file.",
}

# Tools that are always blocked
BLOCKED_TOOLS = {
    "run_shell",  # never allow arbitrary shell — too dangerous
}

# ── Content-injection detection ───────────────────────────────────────────────

INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "override your",
    "forget your",
    "new instructions:",
    "system prompt",
    "act as if",
    "pretend you are",
]

def detect_injection(text: str) -> bool:
    """Return True if the text looks like a prompt-injection attempt."""
    lower = text.lower()
    return any(p in lower for p in INJECTION_PATTERNS)


# ── Dangerous argument patterns ───────────────────────────────────────────────

DANGEROUS_ARGS = [
    "rm -rf", "format", "sudo", "chmod 777",
    "/etc/", "/System/", "/Library/",
    "~/.ssh", "~/.bash_profile", "~/.zshrc",
    "password", "token", "secret", "api_key",
]

def _has_dangerous_args(args: dict) -> bool:
    args_str = str(args).lower()
    return any(p in args_str for p in DANGEROUS_ARGS)


# ── Main safety check ─────────────────────────────────────────────────────────

def check(tool_name: str, args: dict, source: str = "voice") -> SafetyResult:
    """
    Evaluate whether a tool call is safe to execute.

    Parameters
    ----------
    tool_name : str
        Name of the tool to run.
    args : dict
        Arguments the LLM wants to pass to the tool.
    source : str
        Where the instruction came from: 'voice' | 'document' | 'webpage' | 'message'

    Returns
    -------
    SafetyResult with decision ALLOW | CONFIRM | BLOCK
    """

    # 1. Always-blocked tools
    if tool_name in BLOCKED_TOOLS:
        return SafetyResult(
            Decision.BLOCK,
            f"Tool '{tool_name}' is never permitted.",
            "critical"
        )

    # 2. Prompt injection from untrusted sources
    if source != "voice" and detect_injection(str(args)):
        return SafetyResult(
            Decision.BLOCK,
            f"Prompt injection detected in content from '{source}'. Request blocked.",
            "critical"
        )

    # 3. Dangerous argument values
    if _has_dangerous_args(args):
        return SafetyResult(
            Decision.BLOCK,
            "Arguments contain dangerous patterns (system paths, credentials, etc.).",
            "high"
        )

    # 4. Confirm-required tools
    if tool_name in CONFIRM_TOOLS:
        return SafetyResult(
            Decision.CONFIRM,
            CONFIRM_TOOLS[tool_name],
            "medium"
        )

    # 5. Safe tools
    if tool_name in SAFE_TOOLS:
        return SafetyResult(Decision.ALLOW, "Tool is in the safe list.", "low")

    # 6. Unknown tools — ask for confirmation by default
    return SafetyResult(
        Decision.CONFIRM,
        f"Unknown tool '{tool_name}' — needs user confirmation.",
        "medium"
    )


def explain(result: SafetyResult) -> str:
    """Return a human-readable explanation of the safety decision."""
    return (
        f"Decision: {result.decision.upper()} | "
        f"Risk: {result.risk_level} | "
        f"Reason: {result.reason}"
    )
