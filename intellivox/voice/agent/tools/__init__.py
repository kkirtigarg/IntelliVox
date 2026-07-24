"""
agent/tools/__init__.py
Tool registry — maps tool names to callables.
"""
import os
from pathlib import Path
from .browser import open_browser, navigate_url, google_search, youtube_search
from .desktop import open_app, close_app, type_text, press_key, click, take_screenshot, set_volume
from .files   import list_files, read_file, write_file, delete_file, move_file, open_file, find_file
from .document import read_pdf, summarize, answer_question, summarize_codebase
from .computer import computer_use

# ── Tool registry ─────────────────────────────────────────────────────────────
TOOLS = {
    # Browser
    "open_browser":    open_browser,
    "navigate_url":    navigate_url,
    "google_search":   google_search,
    "youtube_search":  youtube_search,

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
    "list_files":      list_files,
    "read_file":       read_file,
    "write_file":      write_file,
    "delete_file":     delete_file,
    "move_file":       move_file,
    "open_file":       open_file,

    # Documents & AI
    "read_pdf":        read_pdf,
    "summarize":       summarize,
    "summarize_codebase": summarize_codebase,
    "answer_question": answer_question,

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
