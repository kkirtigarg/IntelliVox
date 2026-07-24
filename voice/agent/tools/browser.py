"""
agent/tools/browser.py
Browser and URL control tools.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse

log = logging.getLogger("intellivox.browser")

CHROME_APP = "Google Chrome"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_STATE = os.path.expanduser("~/Library/Application Support/Google/Chrome/Local State")


def _escape_apple(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _chrome_profile_directory() -> str:
    """
    Chrome profile folder name (Default, Profile 1, …).
    Override with INTELLIVOX_CHROME_PROFILE=Profile 1
    """
    override = os.environ.get("INTELLIVOX_CHROME_PROFILE", "").strip()
    if override:
        return override
    try:
        with open(CHROME_STATE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("profile", {}).get("last_used") or "Default"
    except OSError:
        return "Default"


def _chrome_is_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-x", "Google Chrome"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _chrome_window_count() -> int:
    script = f'tell application "{CHROME_APP}" to return count of windows'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return 0
    try:
        return int((result.stdout or "0").strip())
    except ValueError:
        return 0


def _navigate_chrome_tab(url: str, *, new_tab: bool = False) -> subprocess.CompletedProcess[str]:
    """Navigate Chrome — reuses the active tab by default to avoid tab spam."""
    url_esc = _escape_apple(url)
    if new_tab:
        tab_cmd = '''
            make new tab at end of tabs
            set URL of active tab to "{url}"
        '''
    else:
        tab_cmd = 'set URL of active tab to "{url}"'

    tab_cmd = tab_cmd.format(url=url_esc)
    script = f'''
    tell application "{CHROME_APP}"
        activate
        if (count of windows) is 0 then
            make new window
        end if
        tell front window
            {tab_cmd}
        end tell
    end tell
    '''
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def _launch_chrome_with_profile(url: str) -> subprocess.CompletedProcess[str]:
    """Cold-start Chrome with the user's last-used profile and URL."""
    profile = _chrome_profile_directory()
    if os.path.isfile(CHROME_BIN):
        log.info("Launching Chrome profile %r → %s", profile, url[:80])
        return subprocess.run(
            [CHROME_BIN, f"--profile-directory={profile}", url],
            capture_output=True,
            text=True,
            timeout=20,
        )
    return subprocess.run(
        ["open", "-a", CHROME_APP, url],
        capture_output=True,
        text=True,
    )


def navigate_url(url: str, browser: str = "chrome", new_tab: bool = False) -> dict:
    """Navigate to a URL in the specified browser using the signed-in session."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    browser_map = {
        "chrome": CHROME_APP,
        "firefox": "Firefox",
        "safari": "Safari",
    }
    app_name = browser_map.get(browser.lower(), CHROME_APP)

    if browser.lower() == "chrome":
        try:
            if _chrome_is_running() or _chrome_window_count() > 0:
                result = _navigate_chrome_tab(url, new_tab=new_tab)
            else:
                result = _launch_chrome_with_profile(url)
                if result.returncode != 0:
                    result = _navigate_chrome_tab(url)

            if result.returncode == 0:
                time.sleep(0.6)
                return {
                    "success": True,
                    "message": f"Opened {url} in {app_name}",
                    "url": url,
                    "profile": _chrome_profile_directory(),
                }
            err = (result.stderr or result.stdout or "Chrome navigation failed").strip()
            log.warning("Chrome navigate failed: %s", err)
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Chrome took too long to open."}
        except Exception as e:
            log.exception("Chrome navigate error")
            return {"success": False, "message": str(e)}

    # Firefox / Safari — AppleScript open location
    url_esc = _escape_apple(url)
    script = f'''
    tell application "{app_name}"
        activate
        open location "{url_esc}"
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Opened {url} in {app_name}", "url": url}

    fallback = subprocess.run(["open", "-a", app_name, url], capture_output=True, text=True)
    if fallback.returncode == 0:
        return {"success": True, "message": f"Opened {url} in {app_name}", "url": url}

    err = (result.stderr or fallback.stderr or "Could not open browser").strip()
    return {"success": False, "message": err}


def open_browser(browser: str = "chrome") -> dict:
    """Open a browser application."""
    browser_map = {
        "chrome": CHROME_APP,
        "firefox": "Firefox",
        "safari": "Safari",
        "edge": "Microsoft Edge",
    }
    app_name = browser_map.get(browser.lower(), CHROME_APP)

    if browser.lower() == "chrome":
        if _chrome_is_running() or _chrome_window_count() > 0:
            script = f'tell application "{app_name}" to activate'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if result.returncode == 0:
                return {"success": True, "message": f"Opened {app_name}"}
        profile = _chrome_profile_directory()
        if os.path.isfile(CHROME_BIN):
            subprocess.Popen([CHROME_BIN, f"--profile-directory={profile}"])
            time.sleep(1.0)
            return {"success": True, "message": f"Opened {app_name} (profile: {profile})"}
        result = subprocess.run(["open", "-a", app_name], capture_output=True, text=True)
        if result.returncode == 0:
            time.sleep(1.0)
            return {"success": True, "message": f"Opened {app_name}"}
        return {"success": False, "message": result.stderr.strip()}

    script = f'tell application "{app_name}" to activate'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Opened {app_name}"}
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
    result = navigate_url(url, browser)
    if result.get("success"):
        result["url"] = url
    return result


def _resolve_first_youtube_video(query: str) -> tuple[str | None, str | None]:
    """Return (watch_url_with_autoplay, error_message)."""
    yt_dlp_bin = shutil.which("yt-dlp")
    cmd_variants: list[list[str]] = []
    if yt_dlp_bin:
        cmd_variants.append([
            yt_dlp_bin, f"ytsearch1:{query}",
            "--print", "webpage_url", "--no-download", "--no-warnings",
        ])
    cmd_variants.append([
        "python", "-m", "yt_dlp", f"ytsearch1:{query}",
        "--print", "webpage_url", "--no-download", "--no-warnings",
    ])

    for cmd in cmd_variants:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            url = (result.stdout or "").strip().splitlines()[0] if result.stdout else ""
            if result.returncode == 0 and url.startswith("https://"):
                sep = "&" if "?" in url else "?"
                return f"{url}{sep}autoplay=1", None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        except Exception:
            continue

    try:
        import httpx

        search_url = (
            "https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(query)
        )
        resp = httpx.get(
            search_url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            seen: set[str] = set()
            for video_id in re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text):
                if video_id not in seen:
                    seen.add(video_id)
                    return f"https://www.youtube.com/watch?v={video_id}&autoplay=1", None
    except Exception as e:
        return None, f"YouTube lookup failed: {e}"

    return None, f"Could not find a YouTube video for '{query}'"


def youtube_play(query: str, browser: str = "chrome") -> dict:
    """Find the top YouTube match and open it in the browser (starts playback)."""
    watch_url, err = _resolve_first_youtube_video(query)
    if not watch_url:
        return {"success": False, "message": err or f"No video found for '{query}'"}

    result = navigate_url(watch_url, browser)
    if result.get("success"):
        result["message"] = f"Playing '{query}' in Google Chrome"
        result["url"] = watch_url
    return result
