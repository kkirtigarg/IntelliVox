"""
agent/tools/__init__.py
Tool registry — maps tool names to callables.
"""
import os
from pathlib import Path
from .browser import open_browser, navigate_url, google_search, youtube_search, youtube_play
from .desktop import open_app, close_app, type_text, press_key, click, take_screenshot, set_volume
from .files   import list_files, read_file, write_file, delete_file, move_file, open_file, find_file, find_compare_pdf_pair
from .document import read_pdf, summarize, answer_question, summarize_codebase, save_summary_file, compare_summarize
from .mail import search_mail, read_mail
from .gmail import open_gmail, read_gmail
from .web_playwright import web_browse
from .computer import computer_use

# ── Tool registry ─────────────────────────────────────────────────────────────
TOOLS = {
    # Browser
    "open_browser":    open_browser,
    "navigate_url":    navigate_url,
    "google_search":   google_search,
    "youtube_search":  youtube_search,
    "youtube_play":    youtube_play,

    # Desktop
    "open_app":        open_app,
    "close_app":       close_app,
    "type_text":       type_text,
    "press_key":       press_key,
    "click":           click,
    "take_screenshot": take_screenshot,
    "set_volume":      set_volume,

    # Files
    "find_file":       find_file,
    "find_compare_pdf_pair": find_compare_pdf_pair,
    "list_files":      list_files,
    "read_file":       read_file,
    "write_file":      write_file,
    "delete_file":     delete_file,
    "move_file":       move_file,
    "open_file":       open_file,

    # Documents & AI
    "read_pdf":        read_pdf,
    "summarize":       summarize,
    "compare_summarize": compare_summarize,
    "compare_and_summarize": compare_summarize,
    "summarize_codebase": summarize_codebase,
    "save_summary_file": save_summary_file,
    "answer_question": answer_question,

    # Mail
    "search_mail":     search_mail,
    "read_mail":       read_mail,
    "open_gmail":      open_gmail,
    "read_gmail":      read_gmail,

    # Web automation (Playwright)
    "web_browse":      web_browse,

    # General computer control (vision + mouse/keyboard loop)
    "computer_use":    computer_use,
}


def run_tool(name: str, args: dict) -> dict:
    """Execute a tool by name with given args."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"success": False, "message": f"Unknown tool: {name}"}
    try:
        return fn(**args)
    except TypeError as e:
        return {"success": False, "message": f"Bad args for {name}: {e}"}
    except Exception as e:
        return {"success": False, "message": f"Tool error: {e}"}
