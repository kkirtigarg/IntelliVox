"""
agent/tools/desktop.py
Desktop control: open apps, type text, keyboard shortcuts, screenshots.
"""
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


def open_app(name: str) -> dict:
    """Open a macOS application by name or alias."""
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

    if app_name == "Mail":
        script = '''
        tell application "Mail"
            activate
            try
                set v to first mail viewer
            on error
                make new viewer at end of mail viewers
            end try
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        time.sleep(0.8)
        if result.returncode == 0:
            return {"success": True, "message": "Opened Mail"}
        # fall through to generic open

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
    """Quit a macOS application."""
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
    import os
    
    desktop_dir = os.path.join(str(Path.home()), "Desktop")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_path = os.path.join(desktop_dir, f"Screenshot_{timestamp}.png")
    
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

def set_volume(level: int) -> dict:
    """Set system volume (0–100)."""
    level = max(0, min(100, level))
    script = f"set volume output volume {level}"
    subprocess.run(["osascript", "-e", script])
    return {"success": True, "message": f"Volume set to {level}%"}
