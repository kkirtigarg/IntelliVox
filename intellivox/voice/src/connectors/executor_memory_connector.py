"""
src/connectors/executor_memory_connector.py
Maps a successfully-executed tool call onto Session/Browser memory updates.
Only ever called after a step has succeeded AND been verified (see
agent/orchestrator.py). Never re-implements a tool — only reads the args
and result a tool already produced.
"""
from urllib.parse import urlparse

from . import browser_memory_connector


def apply(session, browser, tool: str, args: dict, result: dict) -> None:
    if tool in ("open_app", "open_browser"):
        name = args.get("browser") or args.get("name") or ""
        if name:
            session.apply(active_application=name)
            if tool == "open_browser":
                session.apply(active_browser=name)

    elif tool in ("navigate_url", "google_search", "youtube_search"):
        url = args.get("url", "")
        site = urlparse(url).netloc or url
        session.apply(current_website=site, current_url=url)
        browser.push_url(url)
        if tool == "youtube_search":
            browser.last_search_query = args.get("query")

    elif tool == "close_app":
        name = (args.get("name") or "").strip().lower()
        if name and session.state.active_application and name == session.state.active_application.lower():
            session.apply(active_application=None)
        if name and session.state.active_browser and name == session.state.active_browser.lower():
            session.apply(active_browser=None, current_website=None, current_url=None)

    elif tool in ("open_file", "write_file", "read_pdf"):
        path = args.get("path") or result.get("path")
        if path:
            session.apply(current_file=path, current_document=path)

    elif tool == "find_file":
        path = result.get("path")
        if path:
            session.apply(current_file=path)

    elif tool == "computer_use":
        site, url = browser_memory_connector.resolve_current_page(result)
        if site:
            session.apply(current_website=site, current_url=url or session.state.current_url)
            if url:
                browser.push_url(url)
        goal = (args.get("goal") or "").lower()
        if "play" in goal or "video" in goal:
            session.state.current_video.state = "playing"
