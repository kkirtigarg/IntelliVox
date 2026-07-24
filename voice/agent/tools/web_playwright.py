"""
agent/tools/web_playwright.py
Reliable web automation via Playwright (preferred over vision clicking).
"""
from __future__ import annotations

import logging
import re
import urllib.parse

log = logging.getLogger("intellivox.web")


def _infer_url(goal: str) -> str:
    lower = goal.lower()
    if "gmail" in lower or "google mail" in lower:
        q = _extract_query(goal) or ""
        if q:
            enc = urllib.parse.quote_plus(q)
            return f"https://mail.google.com/mail/u/0/#search/{enc}"
        return "https://mail.google.com/mail/u/0/"
    if "youtube" in lower:
        q = _extract_query(goal) or "video"
        return f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}"
    if "google" in lower or "search" in lower:
        q = _extract_query(goal) or goal
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}"
    url_match = re.search(r"https?://[^\s]+", goal)
    if url_match:
        return url_match.group(0).rstrip(".,)")
    q = _extract_query(goal) or goal[:80]
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}"


def _extract_query(goal: str) -> str | None:
    patterns = [
        r"(?:search(?:\s+for)?|find)\s+(.+?)(?:\s+on|\s+in|\s+and|$)",
        r"(?:for)\s+(.+?)(?:\s+on|\s+in|\s+and|$)",
    ]
    lower = goal.lower()
    for pat in patterns:
        m = re.search(pat, lower, re.I)
        if m:
            q = m.group(1).strip(" .")
            if len(q) >= 2:
                return q
    return None


def web_browse(goal: str, url: str = "", headless: bool = False) -> dict:
    """
    Open a web page with Playwright and return visible page text.
    Uses the user's default Chrome profile is NOT shared — opens fresh Chromium.
    For logged-in sites (Gmail), user may need to log in once in the Playwright window.
    """
    if not goal and not url:
        return {"success": False, "message": "No goal or URL provided"}

    target = url.strip() if url else _infer_url(goal)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False,
            "message": "Playwright not installed. Run: pip install playwright && playwright install chromium",
        }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            text = page.inner_text("body")[:12000]
            title = page.title()
            final_url = page.url
            browser.close()
        log.info("web_browse: %s (%d chars)", final_url, len(text))
        return {
            "success": True,
            "url": final_url,
            "title": title,
            "text": text,
            "message": f"Loaded {title or final_url}",
        }
    except Exception as e:
        log.exception("web_browse failed")
        return {"success": False, "message": f"Web browse failed: {e}"}
