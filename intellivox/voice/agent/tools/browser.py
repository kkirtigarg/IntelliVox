"""
agent/tools/browser.py
Browser and URL control tools.
"""
import platform
import subprocess
import webbrowser
import urllib.parse

IS_WINDOWS = platform.system() == "Windows"
IS_MAC     = platform.system() == "Darwin"

# Windows executable names, tried via `start` (shell resolves via PATH / App Paths registry).
WINDOWS_BROWSER_EXE = {
    "chrome":  "chrome",
    "firefox": "firefox",
    "edge":    "msedge",
    "safari":  "chrome",  # not available on Windows — fall back to Chrome
}


def open_browser(browser: str = "chrome") -> dict:
    """Open a browser application."""
    if IS_WINDOWS:
        exe = WINDOWS_BROWSER_EXE.get(browser.lower(), "chrome")
        result = subprocess.run(["cmd", "/c", "start", "", exe], capture_output=True, text=True)
        if result.returncode == 0:
            return {"success": True, "message": f"Opened {browser}"}
        return {"success": False, "message": result.stderr.strip() or f"Could not open {browser}"}

    browser_map = {
        "chrome": "Google Chrome",
        "firefox": "Firefox",
        "safari": "Safari",
        "edge": "Microsoft Edge",
    }
    app_name = browser_map.get(browser.lower(), "Google Chrome")
    script = f'tell application "{app_name}" to activate'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Opened {app_name}"}
    return {"success": False, "message": result.stderr.strip()}


def navigate_url(url: str, browser: str = "chrome") -> dict:
    """Navigate to a URL in the specified browser."""
    # Ensure URL has scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if IS_WINDOWS:
        exe = WINDOWS_BROWSER_EXE.get(browser.lower(), "chrome")
        result = subprocess.run(["cmd", "/c", "start", "", exe, url], capture_output=True, text=True)
        if result.returncode == 0:
            return {"success": True, "message": f"Navigated to {url}"}
        # Fallback: default browser via the stdlib
        try:
            webbrowser.open(url)
            return {"success": True, "message": f"Navigated to {url}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    browser_map = {
        "chrome": "Google Chrome",
        "firefox": "Firefox",
        "safari": "Safari",
    }
    app_name = browser_map.get(browser.lower(), "Google Chrome")
    script = f'''
    tell application "{app_name}"
        activate
        open location "{url}"
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Navigated to {url}"}
    return {"success": False, "message": result.stderr.strip()}


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
