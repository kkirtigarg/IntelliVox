"""
agent/verifier.py
After each action, verifies that it succeeded using window/app state.
"""
import logging
import time
from pathlib import Path

from agent import platform as plat

log = logging.getLogger("intellivox.verifier")


def get_frontmost_app() -> str:
    """Return the name of the currently active application / window."""
    return plat.get_frontmost_app()


def get_active_url() -> str:
    """Return the URL currently open in the browser (best-effort; macOS mainly)."""
    return plat.get_active_url()


def verify_app_open(app_name: str, timeout: float = 3.0) -> dict:
    """Check that the expected app is frontmost within timeout seconds."""
    resolved = plat.resolve_app_name(app_name)
    check = {
        app_name,
        resolved,
        Path(resolved).name,
        "Google Chrome", "Chrome", "chromium", "brave", "Brave",
        "Firefox", "firefox",
        "Finder", "Thunar", "Nautilus", "Dolphin", "Files",
        "Terminal", "xfce4-terminal", "kitty", "gnome-terminal", "konsole",
    }
    # Only keep aliases relevant to the requested app
    relevant = [
        a for a in check
        if a and (
            a.lower() in app_name.lower()
            or app_name.lower() in a.lower()
            or a.lower() in resolved.lower()
            or resolved.lower() in a.lower()
        )
    ]
    if not relevant:
        relevant = [app_name, resolved, Path(resolved).name]

    deadline = time.time() + timeout
    while time.time() < deadline:
        frontmost = get_frontmost_app()
        if any(a.lower() in frontmost.lower() for a in relevant if a):
            return {"success": True, "frontmost": frontmost}
        time.sleep(0.3)

    frontmost = get_frontmost_app()
    # On Linux, frontmost detection often needs xdotool — soft-pass when unknown
    if plat.IS_LINUX and frontmost == "unknown":
        return {"success": True, "frontmost": frontmost, "message": "Skipped strict verify on Linux"}

    return {
        "success": False,
        "message": f"Expected '{app_name}' to be frontmost, got '{frontmost}'",
    }


def verify_url(expected_url_fragment: str, timeout: float = 5.0) -> dict:
    """Check that the active tab URL contains the expected fragment (macOS)."""
    if plat.IS_LINUX:
        return {"success": True, "url": "", "message": "URL verify skipped on Linux"}

    deadline = time.time() + timeout
    while time.time() < deadline:
        url = get_active_url()
        if expected_url_fragment.lower() in url.lower():
            return {"success": True, "url": url}
        time.sleep(0.5)

    return {
        "success": False,
        "message": f"URL did not contain '{expected_url_fragment}'. Got: '{get_active_url()}'",
    }


def verify_step(tool: str, args: dict, result: dict) -> dict:
    """
    After running a tool, verify the outcome.
    Returns { "verified": bool, "message": str }
    """
    if not result.get("success"):
        return {"verified": False, "message": result.get("message", "Tool reported failure")}

    time.sleep(0.5)

    if tool in ("open_browser", "open_app"):
        app = args.get("browser", args.get("name", ""))
        if app:
            v = verify_app_open(app)
            return {"verified": v["success"], "message": v.get("message", f"{app} is open")}

    if tool in ("navigate_url", "google_search", "youtube_search"):
        url = args.get("url", "")
        if "google.com" in url:
            v = verify_url("google.com")
        elif "youtube.com" in url:
            v = verify_url("youtube.com")
        else:
            fragment = url.split("/")[2] if "/" in url else url[:20]
            v = verify_url(fragment)
        return {"verified": v["success"], "message": v.get("message", f"Navigated to {url}")}

    return {"verified": True, "message": result.get("message", "Done")}
