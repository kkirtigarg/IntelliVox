"""Real Windows desktop actuator.

Requires an actual Windows session with a display (pyautogui/pygetwindow),
plus optionally pytesseract + Pillow for OCR-based read_screen_text.

Imported lazily by the CLI only in --real mode so Linux/macOS demo+tests
never need these packages.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from .base import Actuator

try:
    import pyautogui
    import pygetwindow as gw
except ImportError as e:  # pragma: no cover - exercised only on real Windows
    raise ImportError(
        "WindowsGuiActuator requires 'pyautogui' and 'pygetwindow' "
        "(pip install pyautogui pygetwindow), and a real Windows display."
    ) from e

try:
    import pytesseract
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False

# Fail-safe: moving mouse to a corner aborts pyautogui sequences.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# Friendly name -> Windows launch target (Start menu / PATH / common exe).
APP_LAUNCH = {
    "notepad": "notepad.exe",
    "excel": "excel.exe",
    "word": "winword.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "outlook": "outlook.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "paint": "mspaint.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "spotify": "spotify.exe",
    "teams": "ms-teams.exe",
    "slack": "slack.exe",
    "vscode": "code.exe",
    "code": "code.exe",
}


class WindowsGuiActuator(Actuator):
    """Desktop GUI + local filesystem actuator for real Windows machines."""

    def __init__(self, settle_seconds: float = 1.2):
        if sys.platform != "win32":
            # Still constructable for import tests, but warn loudly.
            print(
                "[warn] WindowsGuiActuator is intended for Windows. "
                f"Current platform is {sys.platform!r}."
            )
        self.settle_seconds = settle_seconds

    def open_application(self, app: str) -> dict:
        key = (app or "").strip().lower()
        target = APP_LAUNCH.get(key, app)
        try:
            # `start` is the reliable Windows shell launcher for app names/exes.
            subprocess.Popen(
                ["cmd", "/c", "start", "", target],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return {"observed_window_title": None, "success": False, "error": str(exc)}

        time.sleep(self.settle_seconds)
        needle = key if key else target
        matches = [
            w.title for w in gw.getAllWindows()
            if w.title and needle.lower() in w.title.lower()
        ]
        # Fallbacks: exe stem often appears in title (Notepad, Chrome, …)
        if not matches:
            stem = Path(target).stem.lower()
            matches = [
                w.title for w in gw.getAllWindows()
                if w.title and stem in w.title.lower()
            ]
        return {
            "observed_window_title": matches[0] if matches else None,
            "success": bool(matches),
            "launched": target,
        }

    def list_windows(self) -> list[str]:
        return [w.title for w in gw.getAllWindows() if w.title]

    def focus_window(self, title_contains: str) -> dict:
        matches = [
            w for w in gw.getAllWindows()
            if w.title and title_contains.lower() in w.title.lower()
        ]
        if not matches:
            return {"observed_window_title": None, "success": False}
        win = matches[0]
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
        except Exception as exc:  # noqa: BLE001 — pygetwindow can raise oddly
            return {
                "observed_window_title": win.title,
                "success": False,
                "error": str(exc),
            }
        time.sleep(0.3)
        return {"observed_window_title": win.title, "success": True}

    def screenshot(self) -> dict:
        img = pyautogui.screenshot()
        path = os.path.join(
            os.environ.get("TEMP", "."),
            f"agent_screenshot_{int(time.time())}.png",
        )
        img.save(path)
        return {"path": path, "success": True}

    def read_screen_text(self) -> str:
        if not _HAS_OCR:
            return ""
        img = pyautogui.screenshot()
        return pytesseract.image_to_string(img)

    def click(self, x: int, y: int) -> dict:
        pyautogui.click(int(x), int(y))
        return {"success": True, "x": int(x), "y": int(y)}

    def type_text(self, text: str) -> dict:
        # typewrite is ASCII-oriented; use write for broader Unicode on Windows.
        try:
            pyautogui.write(text, interval=0.02)
        except Exception:
            pyautogui.typewrite(text, interval=0.02)
        return {"success": True, "typed_len": len(text)}

    def open_file(self, path: str) -> dict:
        exists = os.path.exists(path)
        if exists:
            os.startfile(path)  # type: ignore[attr-defined]
            time.sleep(0.5)
        return {"success": exists, "path": path}

    def file_read(self, path: str) -> dict:
        if not os.path.exists(path):
            return {"success": False, "content": None}
        try:
            with open(path, "r", errors="ignore", encoding="utf-8") as f:
                return {"success": True, "content": f.read()}
        except OSError as exc:
            return {"success": False, "content": None, "error": str(exc)}

    def file_write(self, path: str, content: str, overwrite: bool) -> dict:
        if os.path.exists(path) and not overwrite:
            return {"success": False, "reason": "file exists and overwrite=False"}
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "path": path, "bytes": len(content)}

    def file_delete(self, path: str) -> dict:
        existed = os.path.exists(path)
        if existed:
            try:
                os.remove(path)
            except OSError as exc:
                return {"success": False, "path": path, "error": str(exc)}
        return {"success": existed, "path": path}

    def browser_navigate(self, url: str, browser: str | None = None) -> dict:
        # Lightweight fallback when BrowserActuator isn't composed in:
        # open the system default browser.
        _ = browser
        try:
            webbrowser.open(url)
            return {"success": True, "url": url, "status": 200}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "url": url, "error": str(exc)}

    def read_page_text(self) -> str:
        # Without Playwright we can only OCR the screen.
        return self.read_screen_text()

    def click_page_element(self, selector: str) -> dict:
        return {
            "success": False,
            "selector": selector,
            "error": "Use BrowserActuator (Playwright) for reliable DOM clicks.",
        }

    def form_submit(self, url: str, fields: dict) -> dict:
        return {
            "success": False,
            "error": "Use BrowserActuator (Playwright) for form_submit.",
        }

    def send_message(self, to: str, subject: str, body: str) -> dict:
        # Opens the default mail client via mailto: — user still confirms send.
        mailto = f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"
        try:
            os.startfile(mailto)  # type: ignore[attr-defined]
            return {"success": True, "to": to, "via": "mailto"}
        except Exception as exc:  # noqa: BLE001
            if shutil.which("cmd"):
                subprocess.Popen(["cmd", "/c", "start", "", mailto], shell=False)
                return {"success": True, "to": to, "via": "mailto"}
            return {"success": False, "error": str(exc)}
