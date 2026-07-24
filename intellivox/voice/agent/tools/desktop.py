"""
agent/tools/desktop.py
Desktop control: open apps, type text, keyboard shortcuts, screenshots.
"""
import os
import platform
import subprocess
import time
import base64
from io import BytesIO

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except Exception:
    PYAUTOGUI_AVAILABLE = False

try:
    from PIL import ImageGrab
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

IS_WINDOWS = platform.system() == "Windows"
IS_MAC     = platform.system() == "Darwin"


# ── App launcher ──────────────────────────────────────────────────────────────

APP_ALIASES = {
    "chrome":       "Google Chrome",
    "browser":      "Google Chrome",
    "firefox":      "Firefox",
    "safari":       "Safari",
    "terminal":     "Terminal",
    "finder":       "Finder",
    "notes":        "Notes",
    "calendar":     "Calendar",
    "mail":         "Mail",
    "messages":     "Messages",
    "spotify":      "Spotify",
    "vscode":       "Visual Studio Code",
    "code":         "Visual Studio Code",
    "word":         "Microsoft Word",
    "excel":        "Microsoft Excel",
    "powerpoint":   "Microsoft PowerPoint",
    "numbers":      "Numbers",
    "pages":        "Pages",
    "keynote":      "Keynote",
    "textedit":     "TextEdit",
    "preview":      "Preview",
    "calculator":   "Calculator",
    "slack":        "Slack",
    "zoom":         "Zoom",
    "system preferences": "System Preferences",
    "settings":     "System Preferences",
    "activity monitor": "Activity Monitor",
}

# Windows: map aliases to the executable / shell-recognized name used to launch them.
WINDOWS_APP_ALIASES = {
    "chrome":       "chrome",
    "browser":      "chrome",
    "firefox":      "firefox",
    "edge":         "msedge",
    "terminal":     "wt",
    "cmd":          "cmd",
    "powershell":   "powershell",
    "finder":       "explorer",
    "explorer":     "explorer",
    "notes":        "notepad",
    "notepad":      "notepad",
    "calendar":     "outlookcal:",
    "mail":         "outlookmail:",
    "spotify":      "spotify",
    "vscode":       "code",
    "code":         "code",
    "word":         "winword",
    "excel":        "excel",
    "powerpoint":   "powerpnt",
    "calculator":   "calc",
    "slack":        "slack",
    "zoom":         "zoom",
    "settings":     "ms-settings:",
    "system preferences": "ms-settings:",
    "task manager": "taskmgr",
    "activity monitor": "taskmgr",
    "paint":        "mspaint",
}


def _open_app_windows(name: str) -> dict:
    target = WINDOWS_APP_ALIASES.get(name.lower().strip(), name)
    try:
        # ms-settings:, outlookcal: etc. are URI shell verbs — os.startfile handles them.
        # Plain executable names (chrome, notepad, calc, ...) are resolved via PATH/App Paths.
        os.startfile(target)  # noqa: S606 - user-directed desktop automation
        time.sleep(1.0)
        return {"success": True, "message": f"Opened {name}"}
    except OSError:
        pass

    # Fallback: let the shell resolve it (handles App Paths registry entries `start` knows about)
    result = subprocess.run(["cmd", "/c", "start", "", target], capture_output=True, text=True, shell=False)
    if result.returncode == 0:
        time.sleep(1.0)
        return {"success": True, "message": f"Opened {name}"}
    return {"success": False, "message": f"Could not open '{name}'. Make sure it's installed and on PATH."}


def _close_app_windows(name: str) -> dict:
    target = WINDOWS_APP_ALIASES.get(name.lower().strip(), name)
    image_name = target if target.lower().endswith(".exe") else f"{target}.exe"
    result = subprocess.run(["taskkill", "/IM", image_name, "/F"], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Closed {name}"}
    return {"success": False, "message": result.stderr.strip() or result.stdout.strip()}


def open_app(name: str) -> dict:
    """Open an application by name or alias."""
    if IS_WINDOWS:
        return _open_app_windows(name)

    app_name = APP_ALIASES.get(name.lower().strip(), name)

    # Special case: Finder needs a new window opened explicitly
    if app_name == "Finder":
        script = '''
        tell application "Finder"
            activate
            make new Finder window
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        time.sleep(0.8)
        return {"success": True, "message": "Opened Finder"}

    # Primary method: `open -a` — works without special permissions
    result = subprocess.run(["open", "-a", app_name], capture_output=True, text=True)
    if result.returncode == 0:
        time.sleep(1.0)  # give app time to open
        # Bring to front via AppleScript (best effort)
        subprocess.run(["osascript", "-e", f'tell application "{app_name}" to activate'],
                       capture_output=True)
        return {"success": True, "message": f"Opened {app_name}"}

    # Fallback: AppleScript activate
    result2 = subprocess.run(["osascript", "-e", f'tell application "{app_name}" to activate'],
                             capture_output=True, text=True)
    if result2.returncode == 0:
        time.sleep(0.8)
        return {"success": True, "message": f"Opened {app_name}"}

    return {"success": False, "message": f"Could not open '{app_name}'. Check the app name or grant Automation permissions in System Settings → Privacy & Security → Automation."}


def close_app(name: str) -> dict:
    """Quit an application."""
    if IS_WINDOWS:
        return _close_app_windows(name)

    app_name = APP_ALIASES.get(name.lower().strip(), name)
    script = f'tell application "{app_name}" to quit'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Closed {app_name}"}
    return {"success": False, "message": result.stderr.strip()}


# ── Keyboard / typing ─────────────────────────────────────────────────────────

def type_text(text: str) -> dict:
    """Type text at the current cursor position."""
    if not PYAUTOGUI_AVAILABLE:
        return {"success": False, "message": "pyautogui not available"}
    pyautogui.write(text, interval=0.03)
    return {"success": True, "message": f"Typed: {text!r}"}


def press_key(key: str) -> dict:
    """Press a keyboard key or shortcut (e.g., 'enter', 'cmd+t')."""
    if not PYAUTOGUI_AVAILABLE:
        return {"success": False, "message": "pyautogui not available"}
    keys = [k.strip() for k in key.lower().split("+")]
    if not IS_MAC:
        # "cmd" is the mac modifier convention used by the planner/vision prompts;
        # translate it to the Windows/Linux equivalent.
        keys = ["ctrl" if k == "cmd" else k for k in keys]
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)
    return {"success": True, "message": f"Pressed: {key}"}


def click(x: int, y: int) -> dict:
    """Click at screen coordinates."""
    if not PYAUTOGUI_AVAILABLE:
        return {"success": False, "message": "pyautogui not available"}
    pyautogui.click(x, y)
    return {"success": True, "message": f"Clicked ({x}, {y})"}


# ── Screenshot ────────────────────────────────────────────────────────────────

def take_screenshot() -> dict:
    """Take a screenshot, save it to the Desktop with a timestamp, and return the path."""
    import datetime
    from pathlib import Path

    desktop_dir = os.path.join(str(Path.home()), "Desktop")
    os.makedirs(desktop_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_path = os.path.join(desktop_dir, f"Screenshot_{timestamp}.png")

    if not IS_MAC:
        if not PIL_AVAILABLE:
            return {"success": False, "message": "Pillow not available for screenshots"}
        try:
            img = ImageGrab.grab()
            img.save(file_path, "PNG")
            with open(file_path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            return {"success": True, "screenshot_b64": data, "path": file_path, "message": "Screenshot saved to Desktop"}
        except Exception as e:
            return {"success": False, "message": f"Screenshot failed: {e}"}

    result = subprocess.run(
        ["screencapture", "-x", "-t", "png", file_path],
        capture_output=True
    )
    if result.returncode == 0:
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {"success": True, "screenshot_b64": data, "path": file_path, "message": "Screenshot saved to Desktop"}

    # Fallback to /tmp
    tmp_path = "/tmp/intellivox_screen.png"
    result_tmp = subprocess.run(
        ["screencapture", "-x", "-t", "png", tmp_path],
        capture_output=True
    )
    if result_tmp.returncode == 0:
        with open(tmp_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {"success": True, "screenshot_b64": data, "path": tmp_path, "message": "Screenshot saved to temp folder"}
    return {"success": False, "message": "screencapture failed"}


# ── Volume ────────────────────────────────────────────────────────────────────

def _set_volume_windows(level: int) -> dict:
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return {"success": True, "message": f"Volume set to {level}%"}
    except Exception as e:
        return {"success": False, "message": f"Could not set volume (install 'pycaw'): {e}"}


def set_volume(level: int) -> dict:
    """Set system volume (0–100)."""
    level = max(0, min(100, level))
    if IS_WINDOWS:
        return _set_volume_windows(level)
    script = f"set volume output volume {level}"
    subprocess.run(["osascript", "-e", script])
    return {"success": True, "message": f"Volume set to {level}%"}
