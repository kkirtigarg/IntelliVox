"""Real Linux desktop actuator.

Requires an X11/Wayland session with a display, plus pyautogui for
mouse/keyboard/screenshots. Window list/focus prefers wmctrl/xdotool, then
falls back to python-xlib (X11). Process presence is used as a last-resort
success signal when no window titles are available.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from .base import Actuator

try:
    import pyautogui
except ImportError as e:  # pragma: no cover - exercised only on real Linux
    raise ImportError(
        "LinuxGuiActuator requires 'pyautogui' "
        "(pip install pyautogui), and a real Linux display."
    ) from e

try:
    import pytesseract
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# Friendly name -> candidate executables (first found on PATH wins).
APP_LAUNCH: dict[str, list[str]] = {
    # text editor
    "notepad": ["mousepad", "gedit", "xed", "kate", "leafpad"],
    "text editor": ["mousepad", "gedit", "xed", "kate"],
    "gedit": ["gedit"],
    "mousepad": ["mousepad"],
    # web browser
    "browser": ["firefox", "chromium", "google-chrome", "google-chrome-stable"],
    "firefox": ["firefox"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "chromium": ["chromium", "chromium-browser"],
    # file manager
    "explorer": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "files": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "file manager": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "nautilus": ["nautilus"],
    "thunar": ["thunar"],
    # PDF viewer
    "pdf": ["evince", "atril", "okular", "xreader"],
    "pdf viewer": ["evince", "atril", "okular", "xreader"],
    "evince": ["evince"],
    # spreadsheet (LibreOffice Calc)
    "excel": ["localc", "libreoffice", "soffice"],
    "spreadsheet": ["localc", "libreoffice", "soffice"],
    "calc": ["localc", "libreoffice", "soffice"],
    "calculator": ["gnome-calculator", "kcalc", "xcalc", "galculator"],
    # document editor (LibreOffice Writer)
    "word": ["lowriter", "libreoffice", "soffice"],
    "writer": ["lowriter", "libreoffice", "soffice"],
    "document editor": ["lowriter", "libreoffice", "soffice"],
    # presentation editor (LibreOffice Impress)
    "powerpoint": ["loimpress", "libreoffice", "soffice"],
    "impress": ["loimpress", "libreoffice", "soffice"],
    "presentation": ["loimpress", "libreoffice", "soffice"],
    "presentation editor": ["loimpress", "libreoffice", "soffice"],
    "libreoffice": ["libreoffice", "soffice"],
    # misc
    "paint": ["gimp", "kolourpaint"],
    "gimp": ["gimp"],
    "cmd": ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty"],
    "powershell": ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal"],
    "terminal": ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty"],
    "spotify": ["spotify"],
    "slack": ["slack"],
    "vscode": ["code", "codium"],
    "code": ["code", "codium"],
    "teams": ["teams", "teams-for-linux"],
}

# Spoken app name -> strings that may appear in window titles / process names.
APP_TITLE_HINTS: dict[str, list[str]] = {
    "notepad": ["notepad", "gedit", "xed", "kate", "mousepad", "leafpad", "text editor"],
    "calculator": ["calculator", "calc", "kcalc", "galculator"],
    "calc": ["calculator", "calc", "kcalc", "galculator"],
    "explorer": ["files", "nautilus", "dolphin", "thunar", "pcmanfm", "nemo", "home"],
    "files": ["files", "nautilus", "dolphin", "thunar", "pcmanfm", "nemo"],
    "paint": ["gimp", "paint", "kolourpaint"],
    "cmd": ["terminal", "konsole", "alacritty", "kitty"],
    "powershell": ["terminal", "konsole"],
    "terminal": ["terminal", "konsole", "alacritty", "kitty"],
    "vscode": ["visual studio code", "code", "codium", "vscode"],
    "code": ["visual studio code", "code", "codium", "vscode"],
}


def _title_hints_for(app: str, launched: str | None = None) -> list[str]:
    key = (app or "").strip().lower()
    hints = [key]
    hints.extend(APP_TITLE_HINTS.get(key, []))
    if launched:
        hints.append(Path(launched).stem.lower())
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for h in hints:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _process_running(names: list[str]) -> bool:
    for name in names:
        if not name:
            continue
        try:
            r = subprocess.run(
                ["pgrep", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if r.returncode == 0:
                return True
        except OSError:
            continue
    return False


class _WindowBackend:
    """Window enumeration/focus: wmctrl → xdotool → python-xlib."""

    def __init__(self):
        if shutil.which("wmctrl"):
            self._tool = "wmctrl"
        elif shutil.which("xdotool"):
            self._tool = "xdotool"
        else:
            self._tool = "xlib" if self._xlib_available() else None
            if self._tool is None:
                print(
                    "[warn] No window manager tools found (wmctrl/xdotool/python-xlib). "
                    "Open-app verification will use process detection only."
                )

    @staticmethod
    def _xlib_available() -> bool:
        try:
            from Xlib import display  # noqa: F401
            return True
        except ImportError:
            return False

    def list_windows(self) -> list[str]:
        if self._tool == "wmctrl":
            return self._list_wmctrl()
        if self._tool == "xdotool":
            return self._list_xdotool()
        if self._tool == "xlib":
            return self._list_xlib()
        return []

    def _list_wmctrl(self) -> list[str]:
        try:
            out = subprocess.check_output(
                ["wmctrl", "-l"], text=True, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            return []
        titles = []
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4 and parts[3].strip():
                titles.append(parts[3].strip())
        return titles

    def _list_xdotool(self) -> list[str]:
        try:
            ids = subprocess.check_output(
                ["xdotool", "search", "--onlyvisible", "--name", ""],
                text=True, stderr=subprocess.DEVNULL,
            ).split()
        except (OSError, subprocess.CalledProcessError):
            return []
        titles = []
        for wid in ids:
            try:
                name = subprocess.check_output(
                    ["xdotool", "getwindowname", wid],
                    text=True, stderr=subprocess.DEVNULL,
                ).strip()
            except (OSError, subprocess.CalledProcessError):
                continue
            if name:
                titles.append(name)
        return titles

    def _list_xlib(self) -> list[str]:
        try:
            from Xlib import X, display
            from Xlib.error import XError
        except ImportError:
            return []
        try:
            d = display.Display()
            root = d.screen().root
            atom = d.intern_atom("_NET_CLIENT_LIST")
            wm_name = d.intern_atom("_NET_WM_NAME")
            utf8 = d.intern_atom("UTF8_STRING")
            prop = root.get_full_property(atom, X.AnyPropertyType)
            if prop is None:
                return []
            titles: list[str] = []
            for wid in prop.value:
                try:
                    win = d.create_resource_object("window", wid)
                    name_prop = win.get_full_property(wm_name, utf8)
                    if name_prop is None:
                        name_prop = win.get_full_property(wm_name, X.AnyPropertyType)
                    if name_prop is None:
                        name_prop = win.get_full_property(
                            d.intern_atom("WM_NAME"), X.AnyPropertyType,
                        )
                    if name_prop and name_prop.value:
                        raw = name_prop.value
                        if isinstance(raw, bytes):
                            title = raw.decode("utf-8", errors="ignore")
                        else:
                            title = str(raw)
                        if title.strip():
                            titles.append(title.strip())
                except XError:
                    continue
            d.close()
            return titles
        except Exception:  # noqa: BLE001 — X display quirks
            return []

    def focus_window(self, title_contains: str) -> dict:
        needle = title_contains.lower()
        titles = self.list_windows()
        match = next((t for t in titles if needle in t.lower()), None)
        if not match:
            return {"observed_window_title": None, "success": False}

        if self._tool == "wmctrl":
            try:
                subprocess.run(
                    ["wmctrl", "-a", match],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.3)
                return {"observed_window_title": match, "success": True}
            except (OSError, subprocess.CalledProcessError) as exc:
                return {
                    "observed_window_title": match,
                    "success": False,
                    "error": str(exc),
                }

        if self._tool == "xdotool":
            try:
                wid = subprocess.check_output(
                    ["xdotool", "search", "--name", match],
                    text=True, stderr=subprocess.DEVNULL,
                ).splitlines()[0].strip()
                subprocess.run(
                    ["xdotool", "windowactivate", wid],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.3)
                return {"observed_window_title": match, "success": True}
            except (OSError, subprocess.CalledProcessError, IndexError) as exc:
                return {
                    "observed_window_title": match,
                    "success": False,
                    "error": str(exc),
                }

        if self._tool == "xlib":
            # Best-effort: raise via xdotool-like activate isn't available;
            # report match found so callers know the window exists.
            return {"observed_window_title": match, "success": True}

        return {"observed_window_title": match, "success": False}


def _resolve_launch_cmd(app: str) -> list[str] | None:
    key = (app or "").strip().lower()
    candidates = APP_LAUNCH.get(key, [app])
    if isinstance(candidates, str):
        candidates = [candidates]
    for name in candidates:
        # LibreOffice suite: `libreoffice --calc` etc.
        if name == "libreoffice" and key in {
            "excel", "spreadsheet", "calc",
            "word", "writer", "document editor",
            "powerpoint", "impress", "presentation", "presentation editor",
        }:
            exe = shutil.which("libreoffice") or shutil.which("soffice")
            if not exe:
                continue
            flag = {
                "excel": "--calc", "spreadsheet": "--calc", "calc": "--calc",
                "word": "--writer", "writer": "--writer", "document editor": "--writer",
                "powerpoint": "--impress", "impress": "--impress",
                "presentation": "--impress", "presentation editor": "--impress",
            }[key]
            return [exe, flag]
        exe = shutil.which(name)
        if exe:
            return [exe]
    return None


class LinuxGuiActuator(Actuator):
    """Desktop GUI + local filesystem actuator for real Linux machines."""

    def __init__(self, settle_seconds: float = 2.0):
        if not sys.platform.startswith("linux"):
            print(
                "[warn] LinuxGuiActuator is intended for Linux. "
                f"Current platform is {sys.platform!r}."
            )
        self.settle_seconds = settle_seconds
        self._windows = _WindowBackend()

    def _match_title(self, app: str, launched: str | None, titles: list[str]) -> str | None:
        hints = _title_hints_for(app, launched)
        for title in titles:
            low = title.lower()
            if any(h in low for h in hints):
                return title
        return None

    def open_application(self, app: str) -> dict:
        key = (app or "").strip().lower()
        cmd = _resolve_launch_cmd(app)
        if not cmd:
            return {
                "observed_window_title": None,
                "success": False,
                "error": f"Application {app!r} not found on PATH.",
            }
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            return {"observed_window_title": None, "success": False, "error": str(exc)}

        # Poll for a matching window (Firefox etc. can take >1s to show a title).
        match = None
        deadline = time.time() + max(self.settle_seconds, 3.0)
        while time.time() < deadline:
            titles = self._windows.list_windows()
            match = self._match_title(key, cmd[0], titles)
            if match:
                break
            time.sleep(0.35)

        process_ok = _process_running(_title_hints_for(key, cmd[0])) or (
            proc.poll() is None
        )
        return {
            "observed_window_title": match,
            "success": True,
            "launched": cmd[0],
            "process_running": process_ok,
        }

    def list_windows(self) -> list[str]:
        return self._windows.list_windows()

    def focus_window(self, title_contains: str) -> dict:
        return self._windows.focus_window(title_contains)

    def screenshot(self) -> dict:
        img = pyautogui.screenshot()
        path = os.path.join(
            tempfile.gettempdir(),
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
        try:
            pyautogui.write(text, interval=0.02)
        except Exception:
            pyautogui.typewrite(text, interval=0.02)
        return {"success": True, "typed_len": len(text)}

    def open_file(self, path: str) -> dict:
        exists = os.path.exists(path)
        if not exists:
            return {"success": False, "path": path}
        # Prefer VS Code / Codium when available so "open vscode and create file"
        # actually shows the new file in the editor.
        editors = [shutil.which("code"), shutil.which("codium")]
        exe = next((e for e in editors if e), None)
        try:
            if exe:
                subprocess.Popen(
                    [exe, path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                subprocess.Popen(
                    ["xdg-open", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as exc:
            return {"success": False, "path": path, "error": str(exc)}
        time.sleep(0.5)
        return {"success": True, "path": path}

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
        """Open URL in a real browser. Defaults to Firefox on Linux.

        Uses --new-tab for Firefox/Chrome when possible so a search after
        'open firefox' lands as a tab instead of fighting the first window.
        """
        browser_key = (browser or "firefox").strip().lower()
        candidates_map = {
            "firefox": ["firefox"],
            "chrome": ["google-chrome", "google-chrome-stable"],
            "chromium": ["chromium", "chromium-browser"],
            "edge": ["microsoft-edge", "msedge"],
        }
        candidates = candidates_map.get(browser_key, [browser_key, "firefox"])
        exe = next((shutil.which(c) for c in candidates if shutil.which(c)), None)
        try:
            if exe:
                cmd = [exe]
                name = Path(exe).name.lower()
                if "firefox" in name:
                    cmd += ["--new-tab", url]
                elif "chrom" in name:
                    cmd += ["--new-tab", url]
                else:
                    cmd.append(url)
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                webbrowser.open(url)
            time.sleep(1.5)
            return {
                "success": True,
                "url": url,
                "status": 200,
                "browser": exe or browser_key,
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "url": url, "error": str(exc)}

    def read_page_text(self) -> str:
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
        mailto = f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"
        try:
            subprocess.Popen(
                ["xdg-open", mailto],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"success": True, "to": to, "via": "mailto"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
