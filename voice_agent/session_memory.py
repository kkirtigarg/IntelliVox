"""Session memory for multi-turn follow-ups.

Remembers recent user utterances, actions, and the last Google search so
commands like "open the first link" work after a search.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse


@dataclass
class SessionMemory:
    last_transcript: str = ""
    last_search_query: str | None = None
    last_url: str | None = None
    last_app: str | None = None
    recent_transcripts: list[str] = field(default_factory=list)
    recent_actions: list[dict[str, Any]] = field(default_factory=list)

    def remember_transcript(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.last_transcript = text
        self.recent_transcripts.append(text)
        self.recent_transcripts = self.recent_transcripts[-12:]

    def remember_action(self, category: str, args: dict, result: dict | None = None) -> None:
        entry = {"category": category, "args": dict(args), "result": result or {}}
        self.recent_actions.append(entry)
        self.recent_actions = self.recent_actions[-20:]

        if category == "open_app":
            self.last_app = args.get("app")
        if category == "browser_navigate":
            url = (result or {}).get("url") or args.get("url")
            if url:
                self.last_url = url
                q = _query_from_search_url(url)
                if q:
                    self.last_search_query = q
        if category == "open_search_result":
            url = (result or {}).get("url")
            if url:
                self.last_url = url
            if args.get("query"):
                self.last_search_query = args["query"]

    def context_blob(self) -> str:
        """Plain text context for the planner / LLM."""
        parts = []
        if self.last_search_query:
            parts.append(f"last_google_search_query={self.last_search_query!r}")
        if self.last_url:
            parts.append(f"last_url={self.last_url!r}")
        if self.last_app:
            parts.append(f"last_app={self.last_app!r}")
        if self.recent_transcripts:
            parts.append("recent_user_requests=" + " | ".join(self.recent_transcripts[-5:]))
        return "\n".join(parts)


def _query_from_search_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if "google." not in parsed.netloc and "duckduckgo." not in parsed.netloc:
            return None
        qs = parse_qs(parsed.query)
        if "q" in qs and qs["q"]:
            return unquote(qs["q"][0]).strip()
    except Exception:  # noqa: BLE001
        return None
    return None


def feeling_lucky_url(query: str) -> str:
    """Google 'I'm Feeling Lucky' — lands on the first organic result."""
    return (
        "https://www.google.com/search?"
        f"q={quote_plus(query.strip())}&btnI=1&hl=en"
    )


def resolve_search_result_url(query: str, index: int = 1) -> str:
    """Resolve the Nth web result for a query.

    index=1 uses Google I'm Feeling Lucky (accurate to Google ranking).
    index>1 tries DuckDuckGo HTML (Google blocks simple scrapers).
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("No search query in memory — search for something first.")
    if index <= 1:
        return feeling_lucky_url(query)

    # Best-effort Nth result via DuckDuckGo HTML version
    import urllib.request

    ddg = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    req = urllib.request.Request(
        ddg,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Could not fetch search results: {exc}") from exc

    # result__a links are the main results on DDG HTML
    hrefs = re.findall(
        r'class="result__a"[^>]*href="(https?://[^"]+)"',
        html,
    )
    if not hrefs:
        hrefs = re.findall(r'uddg=([^&"]+)', html)
        hrefs = [unquote(h) for h in hrefs if h.startswith("http")]

    # Dedupe while preserving order
    seen: set[str] = set()
    clean: list[str] = []
    for h in hrefs:
        if h not in seen and "duckduckgo.com" not in h:
            seen.add(h)
            clean.append(h)
    if index > len(clean):
        raise RuntimeError(
            f"Only found {len(clean)} results; cannot open result #{index}."
        )
    return clean[index - 1]
