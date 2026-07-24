"""Fuzzy match spoken app names to macOS launch names (for ``open -a``)."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class AppEntry:
    canonical: str
    launch_name: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    ratio = SequenceMatcher(None, a, b).ratio() * 100
    if a in b or b in a:
        ratio = max(ratio, 85.0)
    return ratio


# macOS ``open -a`` names + common ASR aliases (offline)
_MAC_APPS: list[tuple[str, tuple[str, ...]]] = [
    ("Visual Studio Code", ("vscode", "vs code", "visual studio code", "vscoat", "vs coat", "code editor")),
    ("Google Chrome", ("chrome", "google chrome", "crome", "chrme", "browser")),
    ("Firefox", ("firefox", "mozilla firefox")),
    ("Safari", ("safari",)),
    ("Terminal", ("terminal", "command line", "console")),
    ("Finder", ("finder", "file manager", "explorer")),
    ("Notes", ("notes", "note app")),
    ("Calendar", ("calendar",)),
    ("Mail", ("mail", "email")),
    ("Messages", ("messages", "imessage")),
    ("Spotify", ("spotify",)),
    ("Microsoft Word", ("word", "microsoft word")),
    ("Microsoft Excel", ("excel", "microsoft excel")),
    ("Microsoft PowerPoint", ("powerpoint", "microsoft powerpoint")),
    ("Numbers", ("numbers",)),
    ("Pages", ("pages",)),
    ("Keynote", ("keynote",)),
    ("TextEdit", ("textedit", "text edit", "notepad", "note pad")),
    ("Preview", ("preview",)),
    ("Calculator", ("calculator", "calcuator", "calcultor")),
    ("Slack", ("slack",)),
    ("Zoom", ("zoom",)),
    ("System Preferences", ("system preferences", "settings", "system settings")),
    ("Activity Monitor", ("activity monitor",)),
    ("Microsoft Edge", ("edge", "microsoft edge")),
]


class ApplicationResolver:
    """Score user-spoken app targets against installed-style macOS app names."""

    def __init__(self) -> None:
        self._entries: list[tuple[AppEntry, tuple[str, ...]]] = []
        if platform.system() == "Darwin":
            for launch_name, aliases in _MAC_APPS:
                entry = AppEntry(canonical=launch_name, launch_name=launch_name)
                self._entries.append((entry, (launch_name, *aliases)))

    def resolve_scored(self, query: str) -> tuple[AppEntry | None, float]:
        q = _norm(query)
        if not q or not self._entries:
            return None, 0.0

        best_entry: AppEntry | None = None
        best_score = 0.0

        for entry, names in self._entries:
            for name in names:
                score = _similarity(q, name)
                if score > best_score:
                    best_score = score
                    best_entry = entry

        return best_entry, best_score
