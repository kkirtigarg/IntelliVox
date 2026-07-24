"""
agent/tools/desktop.py
Desktop control: open apps, type text, keyboard shortcuts, screenshots.
"""
import base64
import datetime
import os
from pathlib import Path

from agent import platform as plat

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except Exception:
    PYAUTOGUI_AVAILABLE = False


# Keep alias map for callers / planner that import APP_ALIASES
APP_ALIASES = plat._MAC_ALIASES if plat.IS_MAC else {
    k: (v[0] if isinstance(v, list) else v)
    for k, v in plat._LINUX_ALIASES.items()
}


def open_app(name: str) -> dict:
    """Open an application by name or alias (macOS / Linux)."""
    return plat.open_app(name)


def close_app(name: str) -> dict:
    """Quit an application."""
    return plat.close_app(name)


# ── Keyboard / typing ─────────────────────────────────────────────────────────

def type_text(text: str) -> dict:
    """Type text at the current cursor position."""
    if not PYAUTOGUI_AVAILABLE:
        return {"success": False, "message": "pyautogui not available"}
    pyautogui.write(text, interval=0.03)
    return {"success": True, "message": f"Typed: {text!r}"}


def press_key(key: str) -> dict:
    """Press a keyboard key or shortcut (e.g., 'enter', 'cmd+t' / 'ctrl+t')."""
    if not PYAUTOGUI_AVAILABLE:
        return {"success": False, "message": "pyautogui not available"}
    key = plat.normalize_hotkey(key)
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
    desktop_dir = os.path.join(str(Path.home()), "Desktop")
    os.makedirs(desktop_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_path = os.path.join(desktop_dir, f"Screenshot_{timestamp}.png")

    if plat.take_screenshot(file_path):
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {
            "success": True,
            "screenshot_b64": data,
            "path": file_path,
            "message": "Screenshot saved to Desktop",
        }

    tmp_path = "/tmp/intellivox_screen.png"
    if plat.take_screenshot(tmp_path):
        with open(tmp_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {
            "success": True,
            "screenshot_b64": data,
            "path": tmp_path,
            "message": "Screenshot saved to temp folder",
        }
    return {"success": False, "message": "Screenshot failed — install scrot/imagemagick or grant screen access"}


# ── Volume ────────────────────────────────────────────────────────────────────

def set_volume(level: int) -> dict:
    """Set system volume (0–100)."""
    return plat.set_volume(level)
