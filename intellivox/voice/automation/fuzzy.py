"""Typo / ASR correction before intent parsing (offline fuzzy)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from automation.app_resolver import ApplicationResolver

_WORD_FIXES: dict[str, str] = {
    "calcuator": "calculator",
    "calcultor": "calculator",
    "crome": "chrome",
    "chrme": "chrome",
    "googel": "google",
    "gogle": "google",
    "serch": "search",
    "serach": "search",
    "serach for": "search for",
    "clse": "close",
    "cloze": "close",
    "mulitply": "multiply",
    "multipication": "multiplication",
    "visual studio coat": "visual studio code",
    "vs coat": "vs code",
    "vscoat": "vscode",
    "note pad": "textedit",
    "crickbuzz": "cricbuzz",
    "crikbuzz": "cricbuzz",
}

_OPEN_PRONOUNS = frozenset({"it", "that", "this", "them"})


_VERB_CANON = {
    "launch": "open",
    "start": "open",
    "run": "open",
    "lookup": "search",
    "find": "search",
    "google": "search",
}


@dataclass
class FuzzyResult:
    text: str
    notes: list[str]


def _fix_words(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    out = text
    low = text.lower()
    for wrong, right in sorted(_WORD_FIXES.items(), key=lambda x: -len(x[0])):
        if wrong in low:
            out = re.sub(re.escape(wrong), right, out, flags=re.IGNORECASE)
            notes.append(f"'{wrong}' → '{right}'")
            low = out.lower()
    return out, notes


def _fuzzy_open_target(text: str, resolver: ApplicationResolver) -> tuple[str, list[str]]:
    m = re.match(
        r"^(?P<pre>(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+)?)(?P<t>.+?)(?P<suf>\s+app)?$",
        text,
        re.I,
    )
    if not m:
        return text, []
    target = m.group("t").strip()
    if target.lower() in _OPEN_PRONOUNS or len(target.strip()) < 3:
        return text, []
    entry, score = resolver.resolve_scored(target)
    if entry and score >= 55 and entry.canonical.lower() != target.lower():
        fixed = f"{m.group('pre')}{entry.launch_name}{m.group('suf') or ''}"
        return fixed.strip(), [f"app '{target}' → '{entry.launch_name}' (score {score:.0f})"]
    return text, []


def correct_transcript(text: str, resolver: ApplicationResolver | None = None) -> FuzzyResult:
    resolver = resolver or ApplicationResolver()
    notes: list[str] = []
    t = (text or "").strip()
    t, n = _fix_words(t)
    notes.extend(n)

    parts = t.split(None, 1)
    if parts:
        v = parts[0].lower()
        if v in _VERB_CANON:
            rest = parts[1] if len(parts) > 1 else ""
            t = f"{_VERB_CANON[v]} {rest}".strip()
            notes.append(f"verb '{v}' → '{_VERB_CANON[v]}'")

    t2, n2 = _fuzzy_open_target(t, resolver)
    notes.extend(n2)
    return FuzzyResult(text=t2, notes=notes)
