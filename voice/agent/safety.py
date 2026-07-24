"""
agent/safety.py
Deterministic, rule-based safety guardrails.
NO LLM is involved in safety decisions — same input always gives same output.

Also assesses whether a spoken request / plan can be completed
safely and confidently before execution begins.
"""
from __future__ import annotations

import re
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


class Assessment(NamedTuple):
    """Pre-execution judgment: can we complete this safely & confidently?"""
    can_proceed: bool
    kind: str          # ok | unsafe | uncertain | impossible
    confidence: str    # high | medium | low
    reason: str
    title: str
    message: str


# ── Tool classification ────────────────────────────────────────────────────────

# Tools that are always safe — no confirmation needed
SAFE_TOOLS = {
    "open_browser", "navigate_url", "google_search", "youtube_search",
    "open_app", "take_screenshot", "list_files", "read_file",
    "press_key", "set_volume", "open_file", "find_file",
    "read_pdf", "summarize", "summarize_codebase", "answer_question",
    "create_dummy_file", "compare_documents", "compare_pdf_with_dummy",
    "compare_open_files", "extract_pdf_to_spreadsheet",
    "update_presentation_from_document", "create_presentation_from_document",
}

# Tools that need explicit user confirmation before running
CONFIRM_TOOLS = {
    "write_file":   "This will write/overwrite a file on your computer.",
    "write_spreadsheet": "This will create or overwrite a spreadsheet file.",
    "move_file":    "This will move or rename a file.",
    "organize_files": "This will organize files by creating folders and moving files according to your instructions.",
    "close_app":    "This will close an application.",
    "type_text":    "This will type text into the active window.",
    "click":        "This will click somewhere on your screen.",
    "computer_use": "This will control your mouse and keyboard autonomously to complete the task — like a human using the computer.",
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

# Multi-word / path fragments — safe as substring checks
_DANGEROUS_SUBSTRINGS = [
    "rm -rf", "chmod 777",
    "/etc/", "/system/", "/library/",
    "~/.ssh", "~/.bash_profile", "~/.zshrc",
]

# Single tokens — require word boundaries so "information" ≠ "format",
# "documentation" ≠ "token", etc.
_DANGEROUS_WORDS = re.compile(
    r"(?<![a-z0-9_])"
    r"(sudo|password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key|"
    r"format\s+(?:the\s+)?(?:disk|drive|hard\s*drive|ssd|system))"
    r"(?![a-z0-9_])",
    re.I,
)


def _has_dangerous_args(args: dict) -> bool:
    """True when args look like shell bombs, system paths, or credential leaks."""
    for value in args.values():
        s = str(value).lower()
        if any(p in s for p in _DANGEROUS_SUBSTRINGS):
            return True
        if _DANGEROUS_WORDS.search(s):
            return True
    return False


# ── Unsafe / low-confidence request patterns ──────────────────────────────────

# Requests we must refuse (cannot complete safely)
_UNSAFE_REQUEST_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Broad mass-delete only (not "delete all PDFs in Downloads")
    (re.compile(
        r"\b(delete|wipe|erase|remove)\s+"
        r"(everything|"
        r"all\s+(my\s+)?(files?|folders?|data|documents?|photos?|pictures?)\b|"
        r"the\s+entire\s+(computer|system|disk|drive|machine)\b|"
        r"all\s+(files?|data)\s+on\s+(my\s+)?(computer|system|disk|drive|machine)\b)",
        re.I,
    ),
     "Mass deletion across your computer is not allowed."),
    (re.compile(r"\b(format|wipe)\s+(the\s+)?(disk|drive|hard\s*drive|ssd|system)\b", re.I),
     "Formatting or wiping disks is not allowed."),
    (re.compile(r"\b(rm\s*-rf|sudo\s+rm)\b", re.I),
     "Destructive shell deletion commands are not allowed."),
    (re.compile(r"\b(send|transfer|wire|pay)\s+(money|cash|funds|bitcoin|crypto)\b", re.I),
     "I can't send money or make financial transfers."),
    (re.compile(r"\b(bank\s+transfer|wire\s+transfer|venmo|paypal\s+send)\b", re.I),
     "I can't perform financial transfers."),
    (re.compile(
        r"\b(share|send|tell|reveal|show)\s+(me\s+)?(your\s+)?"
        r"(password|passwords|credentials|api\s*keys?|private\s+keys?)\b",
        re.I,
    ),
     "I won't handle or reveal passwords or credentials."),
    (re.compile(r"\b(hack|crack|brute[\s-]*force|steal)\b.*\b(password|account|wifi|network)\b", re.I),
     "I can't help with hacking or cracking accounts."),
    (re.compile(r"\b(bypass|disable)\s+(security|firewall|antivirus|auth|authentication|2fa|mfa)\b", re.I),
     "I won't disable security or authentication protections."),
    (re.compile(r"\b(install|download)\s+(malware|ransomware|keylogger|virus|trojan)\b", re.I),
     "I won't install malware or harmful software."),
    (re.compile(r"\b(exfiltrate|steal)\s+(data|files|secrets)\b", re.I),
     "I won't help exfiltrate or steal data."),
]

# Too vague to complete confidently
_UNCERTAIN_REQUEST_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"^\s*(do\s+(it|that|this)|fix\s+it|make\s+it\s+work|you\s+know|same\s+as\s+before)\s*[.!]?\s*$",
        re.I,
    ),
     "That request is too vague — what should I do exactly?"),
    (re.compile(r"^\s*(help|something|anything|whatever)\s*[.!]?\s*$", re.I),
     "I need a clearer task — what would you like me to do?"),
    (re.compile(
        r"\b(organize|organise|sort|tidy)\s+(everything|all\s+files|my\s+entire\s+(computer|system|disk))\b",
        re.I,
    ),
     "Organizing your entire computer is too broad. Name a folder like Downloads or Desktop."),
    (re.compile(r"\b(delete|remove)\s+(some|random|whatever)\s+files?\b", re.I),
     "I need to know exactly which files to delete before I can proceed safely."),
]

# Clearly outside capability
_IMPOSSIBLE_REQUEST_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(fly|teleport|time[\s-]*travel)\b", re.I),
     "That's outside what I can do on your computer."),
    (re.compile(r"\b(call|phone|text|sms)\s+(someone|him|her|them|\d{3,})", re.I),
     "I can't place phone calls or send SMS from here."),
    (re.compile(r"\b(print\s+on\s+paper|physical\s+mail|send\s+a\s+letter)\b", re.I),
     "I can't control physical printers or postal mail confidently."),
]

_KNOWN_TOOLS = SAFE_TOOLS | set(CONFIRM_TOOLS) | BLOCKED_TOOLS

# Args that must be present for confident execution
_REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    "find_file": ("name",),
    "open_app": ("name",),
    "close_app": ("name",),
    "navigate_url": ("url",),
    "google_search": ("query",),
    "youtube_search": ("query",),
    "read_pdf": ("path",),
    "open_file": ("path",),
    "delete_file": ("path",),
    "computer_use": ("goal",),
    "organize_files": ("directory",),
    "update_presentation_from_document": ("source_path",),
    "create_presentation_from_document": ("source_path",),
    "extract_pdf_to_spreadsheet": ("pdf_path",),
}


def _match_patterns(text: str, patterns: list[tuple[re.Pattern, str]]) -> str | None:
    for pattern, reason in patterns:
        if pattern.search(text or ""):
            return reason
    return None


def assess_request(
    transcript: str,
    *,
    asr_confidence: float | None = None,
) -> Assessment:
    """
    Decide whether the spoken request can be completed safely and confidently.
    Call this before (or right after) planning.
    """
    text = (transcript or "").strip()
    if not text:
        return Assessment(
            False, "uncertain", "low",
            "Empty transcript",
            "Couldn't understand",
            "I didn't catch that clearly enough to act. Please say it again.",
        )

    # Very low ASR confidence → don't act on a guess
    if asr_confidence is not None and asr_confidence < 0.45:
        return Assessment(
            False, "uncertain", "low",
            f"ASR confidence {asr_confidence:.2f}",
            "Not confident",
            "I'm not confident I heard you correctly. Please repeat your request.",
        )

    unsafe = _match_patterns(text, _UNSAFE_REQUEST_PATTERNS)
    if unsafe:
        return Assessment(
            False, "unsafe", "low",
            unsafe,
            "Can't do that safely",
            f"I can't complete that safely. {unsafe}",
        )

    if detect_injection(text):
        return Assessment(
            False, "unsafe", "low",
            "Prompt injection in voice transcript",
            "Can't do that safely",
            "That request looks like an attempt to override my instructions, so I won't run it.",
        )

    impossible = _match_patterns(text, _IMPOSSIBLE_REQUEST_PATTERNS)
    if impossible:
        return Assessment(
            False, "impossible", "low",
            impossible,
            "Can't complete that",
            f"I can't complete that request. {impossible}",
        )

    uncertain = _match_patterns(text, _UNCERTAIN_REQUEST_PATTERNS)
    if uncertain:
        return Assessment(
            False, "uncertain", "low",
            uncertain,
            "Need more detail",
            uncertain,
        )

    # Extremely short / non-actionable noise
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) <= 1 and words and words[0] not in {
        "chrome", "firefox", "brave", "finder", "terminal", "safari", "edge",
    }:
        return Assessment(
            False, "uncertain", "low",
            "Transcript too short",
            "Need more detail",
            f"I only heard “{text}”. Please give a fuller instruction.",
        )

    confidence = "high"
    if asr_confidence is not None and asr_confidence < 0.7:
        confidence = "medium"

    return Assessment(
        True, "ok", confidence,
        "Request looks actionable",
        "",
        "",
    )


def assess_plan(plan: dict, transcript: str = "") -> Assessment:
    """
    Decide whether a generated plan can be executed safely and confidently.
    """
    if not isinstance(plan, dict):
        return Assessment(
            False, "uncertain", "low",
            "Invalid plan object",
            "Can't complete that",
            "I couldn't build a reliable plan for that request. Please rephrase it.",
        )

    if plan.get("clarification_needed"):
        q = plan.get("clarification_question") or "Could you rephrase that?"
        return Assessment(
            False, "uncertain", "low",
            "Planner requested clarification",
            "Need more detail",
            q,
        )

    steps = plan.get("steps") or []
    if not steps:
        return Assessment(
            False, "uncertain", "low",
            "Empty plan",
            "Can't complete that",
            "I couldn't figure out safe steps for that request. Please try a clearer instruction.",
        )

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return Assessment(
                False, "uncertain", "low",
                f"Malformed step {i}",
                "Can't complete that",
                "The plan for that task was incomplete, so I won't run it.",
            )
        tool = (step.get("tool") or "").strip()
        args = step.get("args") if isinstance(step.get("args"), dict) else {}

        if not tool:
            return Assessment(
                False, "uncertain", "low",
                f"Missing tool on step {i}",
                "Can't complete that",
                "I don't have a confident action for part of that request.",
            )

        if tool in BLOCKED_TOOLS:
            return Assessment(
                False, "unsafe", "low",
                f"Plan uses blocked tool {tool}",
                "Can't do that safely",
                f"That plan would use '{tool}', which isn't allowed.",
            )

        if tool not in _KNOWN_TOOLS:
            return Assessment(
                False, "uncertain", "low",
                f"Unknown tool {tool}",
                "Can't complete confidently",
                f"I don't confidently support the action '{tool}' for this request.",
            )

        # Unresolved placeholders with no prior step to fill them → low confidence
        for _key, val in args.items():
            if isinstance(val, str) and "{{step_" in val and i == 0:
                return Assessment(
                    False, "uncertain", "low",
                    f"Unresolved placeholder {val} on first step",
                    "Can't complete confidently",
                    "I'm missing a required file or path to do that safely. Please name it more clearly.",
                )

        required = _REQUIRED_ARGS.get(tool, ())
        for req in required:
            val = args.get(req)
            if val is None or (isinstance(val, str) and not val.strip()):
                return Assessment(
                    False, "uncertain", "low",
                    f"Missing required arg {req} for {tool}",
                    "Need more detail",
                    f"I need a clearer {req.replace('_', ' ')} before I can do that confidently.",
                )

        # computer_use with tiny/vague goal
        if tool == "computer_use":
            goal = str(args.get("goal") or "").strip()
            if len(goal) < 8:
                return Assessment(
                    False, "uncertain", "low",
                    "computer_use goal too vague",
                    "Need more detail",
                    "That screen-control goal is too vague for me to do safely. Please be more specific.",
                )

    # Re-check transcript against unsafe patterns (plan might have softened them)
    req = assess_request(transcript)
    if not req.can_proceed and req.kind == "unsafe":
        return req

    return Assessment(
        True, "ok", "high",
        f"Plan OK ({len(steps)} steps)",
        "",
        "",
    )


def assess_step_failure(
    tool: str,
    result: dict,
    *,
    step_index: int,
    total_steps: int,
    consecutive_failures: int,
) -> Assessment | None:
    """
    After a failed / unverified step, decide whether to abort the whole task
    because we can no longer complete it safely or confidently.
    Returns None if execution may continue.
    """
    msg = (result or {}).get("message") or "Step failed"

    # Critical tools: failure means we cannot continue the chain confidently
    critical = {
        "find_file", "read_pdf", "read_file",
        "update_presentation_from_document",
        "create_presentation_from_document",
        "extract_pdf_to_spreadsheet",
        "organize_files",
        "compare_open_files",
        "compare_pdf_with_dummy",
    }
    if tool in critical:
        return Assessment(
            False, "uncertain", "low",
            f"Critical step failed: {tool}",
            "Can't complete confidently",
            f"I couldn't complete that safely — {msg}. Please check the details and try again.",
        )

    if consecutive_failures >= 2:
        return Assessment(
            False, "uncertain", "low",
            f"{consecutive_failures} consecutive failures",
            "Can't complete confidently",
            f"Multiple steps failed, so I'm stopping rather than guessing. Last error: {msg}",
        )

    # Last step failed → task incomplete
    if step_index >= total_steps - 1:
        return Assessment(
            False, "uncertain", "low",
            "Final step failed",
            "Couldn't finish",
            f"I couldn't finish that request confidently. {msg}",
        )

    return None


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
