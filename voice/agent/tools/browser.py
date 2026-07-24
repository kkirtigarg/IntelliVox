"""
agent/tools/browser.py
Browser and URL control tools.
"""
import subprocess
import webbrowser
import urllib.parse


def open_browser(browser: str = "chrome") -> dict:
    """Open a browser application."""
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
