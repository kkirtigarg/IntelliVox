"""
agent/verifier.py
After each action, verifies that it succeeded using a screenshot.
"""
import subprocess
import logging
import time

log = logging.getLogger("intellivox.verifier")


def get_frontmost_app() -> str:
    """Return the name of the currently active macOS application."""
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def get_active_url() -> str:
    """Return the URL currently open in Chrome (best-effort)."""
    script = '''
    tell application "Google Chrome"
        if (count of windows) > 0 then
            return URL of active tab of front window
        end if
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def verify_app_open(app_name: str, timeout: float = 3.0) -> dict:
    """Check that the expected app is frontmost within timeout seconds."""
    app_map = {
        "Google Chrome": ["Google Chrome", "Chrome"],
        "Firefox":       ["Firefox"],
        "Safari":        ["Safari"],
        "Finder":        ["Finder"],
        "Terminal":      ["Terminal"],
    }
    aliases = app_map.get(app_name, [app_name])

    deadline = time.time() + timeout
    while time.time() < deadline:
        frontmost = get_frontmost_app()
        if any(a.lower() in frontmost.lower() for a in aliases):
            return {"success": True, "frontmost": frontmost}
        time.sleep(0.3)

    return {
        "success": False,
        "message": f"Expected '{app_name}' to be frontmost, got '{get_frontmost_app()}'",
    }


def verify_url(expected_url_fragment: str, timeout: float = 5.0) -> dict:
    """Check that Chrome's active tab URL contains the expected fragment."""
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

    time.sleep(0.5)  # brief pause for UI to settle

    if tool in ("open_browser", "open_app"):
        app = args.get("browser", args.get("name", ""))
        browser_app_map = {"chrome": "Google Chrome", "firefox": "Firefox", "safari": "Safari"}
        app_name = browser_app_map.get(app.lower(), app) if app else ""
        if app_name:
            v = verify_app_open(app_name)
            return {"verified": v["success"], "message": v.get("message", f"{app_name} is open")}

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

    # For other tools, trust the result
    return {"verified": True, "message": result.get("message", "Done")}
