"""
agent/tools/browser.py
Browser and URL control tools.
"""
import urllib.parse

from agent import platform as plat


def open_browser(browser: str = "chrome") -> dict:
    """Open a browser application."""
    result = plat.open_app(browser)
    if result.get("success"):
        resolved = plat.resolve_app_name(browser)
        # Be honest when Chrome isn't installed and we opened Brave/Firefox
        asked = browser.lower().strip()
        if "chrome" in asked and "chrome" not in resolved.lower() and "chromium" not in resolved.lower():
            result["message"] = (
                f"Chrome not installed — opened {resolved} instead"
            )
        return result
    # Last resort: default browser via blank page
    fallback = plat.open_url("about:blank")
    if fallback.get("success"):
        fallback["message"] = (
            f"Could not open '{browser}' directly; opened the default browser instead"
        )
    return fallback


def navigate_url(url: str, browser: str = "chrome") -> dict:
    """Navigate to a URL in the specified browser."""
    return plat.open_url(url, browser=browser)


def google_search(query: str, browser: str = "chrome") -> dict:
    """Open a Google search for the given query."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?q={encoded}"
    return navigate_url(url, browser)


def youtube_search(query: str, browser: str = "chrome") -> dict:
    """Open a YouTube search for the given query."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={encoded}"
    return navigate_url(url, browser)
