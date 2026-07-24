"""
agent/tools/browser.py
Browser and URL control tools.
"""
import urllib.parse

from agent import platform as plat


def open_browser(browser: str = "chrome") -> dict:
    """Open a browser application."""
    # Prefer chrome/brave/firefox aliases; fall back to default browser via empty open
    result = plat.open_app(browser)
    if result.get("success"):
        return result
    # Default browser via opening a blank page
    return plat.open_url("about:blank")


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
