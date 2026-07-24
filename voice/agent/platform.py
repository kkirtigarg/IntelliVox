"""
agent/platform.py
Cross-platform helpers for macOS and Linux desktop control.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"
OS_NAME = "macOS" if IS_MAC else ("Linux" if IS_LINUX else platform.system())

HOME = str(Path.home())

# Modifiers: maps voice/LLM shortcuts to the OS-native modifier name used by pyautogui
MOD_KEY = "command" if IS_MAC else "ctrl"


# ── App aliases ───────────────────────────────────────────────────────────────

_MAC_ALIASES = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "browser": "Google Chrome",
    "firefox": "Firefox",
    "safari": "Safari",
    "edge": "Microsoft Edge",
    "brave": "Brave Browser",
    "terminal": "Terminal",
    "finder": "Finder",
    "files": "Finder",
    "notes": "Notes",
    "calendar": "Calendar",
    "mail": "Mail",
    "messages": "Messages",
    "spotify": "Spotify",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "numbers": "Numbers",
    "pages": "Pages",
    "keynote": "Keynote",
    "textedit": "TextEdit",
    "notepad": "TextEdit",
    "preview": "Preview",
    "calculator": "Calculator",
    "slack": "Slack",
    "zoom": "Zoom",
    "system preferences": "System Preferences",
    "settings": "System Preferences",
    "activity monitor": "Activity Monitor",
}

# Linux: values are executable names or .desktop basenames (resolved at runtime)
# Prefer Firefox over Brave when Chrome/Chromium aren't installed (common on Linux).
_LINUX_ALIASES = {
    "chrome": [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "firefox", "brave", "brave-browser",
    ],
    "google chrome": [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "firefox", "brave", "brave-browser",
    ],
    "google-chrome": [
        "google-chrome", "google-chrome-stable", "chromium", "firefox", "brave",
    ],
    "browser": ["firefox", "brave", "brave-browser", "google-chrome", "chromium"],
    "firefox": ["firefox"],
    "brave": ["brave", "brave-browser"],
    "edge": ["microsoft-edge", "microsoft-edge-stable", "firefox", "brave"],
    "safari": ["firefox", "brave"],  # no Safari on Linux — fall back to Firefox
    "terminal": ["xfce4-terminal", "kitty", "gnome-terminal", "konsole", "xterm", "alacritty"],
    "finder": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "files": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "file manager": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "notepad": ["mousepad", "gedit", "kate", "xed", "leafpad", "nano"],
    "textedit": ["mousepad", "gedit", "kate", "xed", "leafpad"],
    "notes": ["mousepad", "gedit", "kate"],
    "calculator": ["galculator", "gnome-calculator", "kcalc", "qalculate-gtk"],
    "vscode": ["code", "code-oss", "codium"],
    "code": ["code", "code-oss", "codium"],
    "word": ["libreoffice", "soffice"],
    "excel": ["libreoffice", "soffice"],
    "powerpoint": ["libreoffice", "soffice"],
    "numbers": ["libreoffice", "soffice"],
    "pages": ["libreoffice", "soffice"],
    "keynote": ["libreoffice", "soffice"],
    "spotify": ["spotify"],
    "slack": ["slack"],
    "zoom": ["zoom"],
    "settings": ["xfce4-settings-manager", "gnome-control-center", "systemsettings"],
    "system preferences": ["xfce4-settings-manager", "gnome-control-center", "systemsettings"],
}


def resolve_app_name(name: str) -> str:
    """Map a friendly alias to a platform-specific app/executable name."""
    key = name.lower().strip()
    # Normalize common spoken forms
    key = re.sub(r"\s+", " ", key)
    key = key.replace("googlechrome", "google chrome")
    if key.startswith("google chrome"):
        key = "google chrome"
    if IS_MAC:
        return _MAC_ALIASES.get(key, _MAC_ALIASES.get(key.replace(" ", ""), name))
    if IS_LINUX:
        candidates = _LINUX_ALIASES.get(key)
        if candidates:
            for cmd in candidates:
                if shutil.which(cmd):
                    return cmd
            # Nothing installed from the list — still return first for a clear error
            return candidates[0]
        # Pass through if already an executable
        if shutil.which(name):
            return name
        # Try first word (e.g. "firefox and open google" → firefox)
        first = key.split()[0] if key else ""
        if first and first in _LINUX_ALIASES:
            return resolve_app_name(first)
        if shutil.which(first):
            return first
        return name
    return name


def browser_is_available(alias: str) -> bool:
    """True if the given browser alias resolves to an installed executable."""
    if IS_MAC:
        app = resolve_app_name(alias)
        return (Path("/Applications") / f"{app}.app").exists() or (
            Path.home() / "Applications" / f"{app}.app"
        ).exists()
    if IS_LINUX:
        resolved = resolve_app_name(alias)
        return bool(shutil.which(resolved))
    return True


def preferred_browser() -> str:
    """Pick a sensible default browser alias (Firefox first on Linux)."""
    for alias in ("firefox", "chrome", "brave", "edge"):
        if browser_is_available(alias):
            return alias
    return "chrome"


def normalize_hotkey(key: str) -> str:
    """Rewrite cmd/command/meta shortcuts to the OS-native modifier."""
    parts = [p.strip().lower() for p in key.split("+")]
    out = []
    for p in parts:
        if p in ("cmd", "command", "meta", "super", "win"):
            out.append(MOD_KEY)
        else:
            out.append(p)
    return "+".join(out)


# ── Installed apps ────────────────────────────────────────────────────────────

def list_installed_apps(limit: int = 40) -> list[str]:
    """Return a sample of installed application names for the planner prompt."""
    apps: list[str] = []
    if IS_MAC:
        try:
            apps = [
                p.stem for p in Path("/Applications").iterdir()
                if p.suffix == ".app"
            ]
        except Exception:
            pass
    elif IS_LINUX:
        seen: set[str] = set()
        for d in (
            Path("/usr/share/applications"),
            Path.home() / ".local/share/applications",
            Path("/var/lib/flatpak/exports/share/applications"),
        ):
            if not d.is_dir():
                continue
            for desk in d.glob("*.desktop"):
                name = desk.stem
                # Prefer Name= from desktop file when available
                try:
                    text = desk.read_text(encoding="utf-8", errors="replace")
                    for line in text.splitlines():
                        if line.startswith("Name=") and not line.startswith("Name["):
                            name = line.split("=", 1)[1].strip()
                            break
                except Exception:
                    pass
                if name and name not in seen:
                    seen.add(name)
                    apps.append(name)
        # Also include common binaries that are installed
        for cmd in (
            "firefox", "brave-browser", "google-chrome", "chromium",
            "thunar", "nautilus", "dolphin", "xfce4-terminal", "kitty",
            "code", "code-oss", "libreoffice", "galculator", "spotify",
        ):
            if shutil.which(cmd) and cmd not in seen:
                seen.add(cmd)
                apps.append(cmd)
    return sorted(apps)[:limit]


# ── Open / close apps ─────────────────────────────────────────────────────────

def open_app(name: str) -> dict:
    """Open an application by name or alias."""
    app = resolve_app_name(name)

    if IS_MAC:
        if app == "Finder":
            script = '''
            tell application "Finder"
                activate
                make new Finder window
            end tell
            '''
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            time.sleep(0.8)
            if result.returncode == 0:
                return {"success": True, "message": "Opened Finder"}
            return {"success": False, "message": result.stderr.strip() or "Failed to open Finder"}

        result = subprocess.run(["open", "-a", app], capture_output=True, text=True)
        if result.returncode == 0:
            time.sleep(1.0)
            subprocess.run(
                ["osascript", "-e", f'tell application "{app}" to activate'],
                capture_output=True,
            )
            return {"success": True, "message": f"Opened {app}"}

        result2 = subprocess.run(
            ["osascript", "-e", f'tell application "{app}" to activate'],
            capture_output=True, text=True,
        )
        if result2.returncode == 0:
            time.sleep(0.8)
            return {"success": True, "message": f"Opened {app}"}
        return {
            "success": False,
            "message": f"Could not open '{app}'. Check the app name or grant Automation permissions.",
        }

    if IS_LINUX:
        # Try gtk-launch with a .desktop id if present
        desktop_id = _find_desktop_id(app)
        if desktop_id:
            result = subprocess.run(
                ["gtk-launch", desktop_id],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                time.sleep(0.8)
                return {"success": True, "message": f"Opened {app}"}

        # Direct executable
        exe = shutil.which(app) or app
        try:
            subprocess.Popen(
                [exe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(0.8)
            return {"success": True, "message": f"Opened {app}"}
        except FileNotFoundError:
            # Last resort: xdg-open a .desktop file
            desk = _find_desktop_path(app)
            if desk:
                result = subprocess.run(["xdg-open", str(desk)], capture_output=True, text=True)
                if result.returncode == 0:
                    time.sleep(0.8)
                    return {"success": True, "message": f"Opened {app}"}
            return {"success": False, "message": f"Could not open '{app}'. Is it installed?"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    return {"success": False, "message": f"open_app not supported on {OS_NAME}"}


def close_app(name: str) -> dict:
    """Quit / kill an application."""
    app = resolve_app_name(name)

    if IS_MAC:
        result = subprocess.run(
            ["osascript", "-e", f'tell application "{app}" to quit'],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return {"success": True, "message": f"Closed {app}"}
        return {"success": False, "message": result.stderr.strip()}

    if IS_LINUX:
        # Prefer graceful wmctrl close, then pkill
        title_hint = Path(app).name
        if shutil.which("wmctrl"):
            subprocess.run(["wmctrl", "-c", title_hint], capture_output=True)
        result = subprocess.run(["pkill", "-f", app], capture_output=True, text=True)
        # pkill returns 1 when no process matched — treat as soft failure
        if result.returncode in (0, 1):
            return {"success": True, "message": f"Closed {app}"}
        return {"success": False, "message": result.stderr.strip() or f"Failed to close {app}"}

    return {"success": False, "message": f"close_app not supported on {OS_NAME}"}


def _find_desktop_path(app: str) -> Path | None:
    names = {app.lower(), Path(app).name.lower(), f"{Path(app).name.lower()}.desktop"}
    for d in (
        Path("/usr/share/applications"),
        Path.home() / ".local/share/applications",
    ):
        if not d.is_dir():
            continue
        for desk in d.glob("*.desktop"):
            if desk.name.lower() in names or desk.stem.lower() in names:
                return desk
            # Match Exec= line
            try:
                for line in desk.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("Exec=") and app in line:
                        return desk
            except Exception:
                pass
    return None


def _find_desktop_id(app: str) -> str | None:
    path = _find_desktop_path(app)
    return path.stem if path else None


# ── Open files / URLs ─────────────────────────────────────────────────────────

def open_path(path: str) -> dict:
    """Open a file or directory with the default application."""
    expanded = str(Path(path).expanduser())
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}

    if IS_MAC:
        result = subprocess.run(["open", expanded], capture_output=True, text=True)
    elif IS_LINUX:
        result = subprocess.run(["xdg-open", expanded], capture_output=True, text=True)
    else:
        return {"success": False, "message": f"open_path not supported on {OS_NAME}"}

    if result.returncode == 0:
        return {"success": True, "message": f"Opened {expanded}"}
    return {"success": False, "message": result.stderr.strip() or f"Failed to open {expanded}"}


def open_url(url: str, browser: str | None = None) -> dict:
    """Open a URL in the default or specified browser."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if browser:
        app = resolve_app_name(browser)
        if IS_MAC:
            result = subprocess.run(["open", "-a", app, url], capture_output=True, text=True)
            if result.returncode == 0:
                return {"success": True, "message": f"Navigated to {url}"}
            return {"success": False, "message": result.stderr.strip()}
        if IS_LINUX:
            exe = shutil.which(app)
            if exe:
                try:
                    subprocess.Popen(
                        [exe, url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return {"success": True, "message": f"Navigated to {url}"}
                except Exception as e:
                    return {"success": False, "message": str(e)}

    # Default handler
    if IS_MAC:
        result = subprocess.run(["open", url], capture_output=True, text=True)
    elif IS_LINUX:
        result = subprocess.run(["xdg-open", url], capture_output=True, text=True)
    else:
        import webbrowser
        ok = webbrowser.open(url)
        return {"success": ok, "message": f"Navigated to {url}" if ok else "Failed to open URL"}

    if result.returncode == 0:
        return {"success": True, "message": f"Navigated to {url}"}
    return {"success": False, "message": result.stderr.strip() or "Failed to open URL"}


# ── Find files ────────────────────────────────────────────────────────────────

_FIND_SKIP_DIR_PARTS = (
    "/node_modules/", "/.git/", "/.venv/", "/venv/", "/__pycache__/",
    "/dist/", "/build/", "/.cache/", "/site-packages/", "/.npm/",
    "/.local/share/Trash/",
)


def find_file(name: str, directory: str | None = None, ext: str = "") -> dict:
    """
    Search for a file by name (Spotlight on macOS, find on Linux).

    ext: optional extension filter without dot, e.g. "pdf", "pptx"
    Skips junk trees like node_modules / venv.
    """
    search_dir = str(Path(directory or HOME).expanduser())
    needle = (name or "").strip()
    ext_norm = (ext or "").lstrip(".").lower()

    # Generic "find a PDF" when name is empty / just an extension word
    if not needle or needle.lower() in {".pdf", "pdf", "*.pdf", "document", "file"}:
        if not ext_norm:
            ext_norm = "pdf" if needle.lower() in {".pdf", "pdf", "*.pdf", ""} else ""
        needle = ""

    matches: list[str] = []

    def _is_junk(p: str) -> bool:
        pl = p.replace("\\", "/").lower()
        return any(part in pl for part in _FIND_SKIP_DIR_PARTS)

    def _collect(raw_lines: list[str]) -> list[str]:
        out = []
        for line in raw_lines:
            p = line.strip()
            if not p or _is_junk(p):
                continue
            if ext_norm and not p.lower().endswith(f".{ext_norm}"):
                # Also allow directories only when not filtering by ext
                continue
            out.append(p)
        return out

    if IS_MAC and shutil.which("mdfind"):
        if needle:
            result = subprocess.run(
                ["mdfind", "-onlyin", search_dir, "-name", needle],
                capture_output=True, text=True,
            )
            matches = _collect(result.stdout.strip().splitlines())
        elif ext_norm:
            result = subprocess.run(
                ["mdfind", "-onlyin", search_dir, f"kMDItemFSName == '*.{ext_norm}'cd"],
                capture_output=True, text=True,
            )
            matches = _collect(result.stdout.strip().splitlines())
        if not matches and search_dir != HOME:
            fallback_q = needle or (f".{ext_norm}" if ext_norm else "")
            if fallback_q:
                result2 = subprocess.run(
                    ["mdfind", "-onlyin", HOME, "-name", fallback_q],
                    capture_output=True, text=True,
                )
                matches = _collect(result2.stdout.strip().splitlines())
    else:
        # find — works everywhere; limit depth for speed
        find_cmd = [
            "find", search_dir,
            "-maxdepth", "6",
            "(",
            "-type", "f",
            "-o", "-type", "d",
            ")",
        ]
        if needle:
            find_cmd += ["-iname", f"*{needle}*"]
        if ext_norm:
            # Prefer files with the extension (still allow name match dirs filtered out later)
            find_cmd = [
                "find", search_dir,
                "-maxdepth", "6",
                "-type", "f",
                "-iname", f"*{needle}*.{ext_norm}" if needle else f"*.{ext_norm}",
            ]
        find_cmd += ["-not", "-path", "*/.*"]
        # Prune junk directories early
        for junk in ("node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build"):
            find_cmd += ["-not", "-path", f"*/{junk}/*"]

        try:
            result = subprocess.run(
                find_cmd,
                capture_output=True, text=True, timeout=20,
            )
            matches = _collect(result.stdout.strip().splitlines())
        except subprocess.TimeoutExpired:
            matches = []

        if not matches and shutil.which("locate") and needle:
            try:
                result2 = subprocess.run(
                    ["locate", "-i", "-l", "40", needle],
                    capture_output=True, text=True, timeout=10,
                )
                matches = _collect([
                    l for l in result2.stdout.strip().splitlines()
                    if l.strip().startswith(HOME)
                ])
            except subprocess.TimeoutExpired:
                matches = []

    if not matches:
        label = needle or (f"*.{ext_norm}" if ext_norm else "file")
        return {"success": False, "message": f"No file found matching '{label}'", "matches": []}

    # Prefer .pptx when searching for presentations / decks; prefer .pdf for docs
    def _rank(p: str) -> tuple:
        pl = p.lower()
        n = (needle or "").lower()
        path_obj = Path(p)
        is_file = path_obj.is_file() if path_obj.exists() else ("." in path_obj.name)
        is_pptx = pl.endswith((".pptx", ".ppt"))
        is_pdf = pl.endswith(".pdf")
        name_is_pptx = n in ("pptx", "ppt", "powerpoint", "presentation", "slides", "deck")
        prefer_type = 0
        if ext_norm == "pdf" or "pdf" in n:
            prefer_type = 0 if is_pdf else 2
        elif name_is_pptx or ext_norm in ("pptx", "ppt"):
            prefer_type = 0 if is_pptx else 2
        elif is_pptx and "ppt" in n:
            prefer_type = 0
        # Prefer real files over directories; prefer Downloads/Documents; prefer shorter paths
        prefer_file = 0 if is_file else 1
        prefer_docs = 0 if ("/downloads/" in pl or "/documents/" in pl) else 1
        # Prefer basename that contains the needle
        base = path_obj.name.lower()
        prefer_name = 0 if (n and n in base) else 1
        try:
            mtime = -path_obj.stat().st_mtime if path_obj.exists() else 0
        except OSError:
            mtime = 0
        return (prefer_type, prefer_file, prefer_name, prefer_docs, len(p.split(os.sep)), mtime, p)

    matches.sort(key=_rank)
    best = matches[0]
    return {
        "success": True,
        "path": best,
        "matches": matches[:5],
        "message": f"Found: {Path(best).name}",
    }


# ── Screenshot ────────────────────────────────────────────────────────────────

def take_screenshot(file_path: str) -> bool:
    """Capture the screen to file_path. Returns True on success."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if IS_MAC and shutil.which("screencapture"):
        result = subprocess.run(
            ["screencapture", "-x", "-t", "png", str(path)],
            capture_output=True,
        )
        return result.returncode == 0 and path.exists()

    if IS_LINUX:
        # Prefer ImageMagick import (X11), then scrot/gnome-screenshot/grim, then pyautogui
        if shutil.which("import") and os.environ.get("DISPLAY"):
            result = subprocess.run(
                ["import", "-window", "root", str(path)],
                capture_output=True,
            )
            if result.returncode == 0 and path.exists():
                return True
        if shutil.which("scrot"):
            result = subprocess.run(["scrot", str(path)], capture_output=True)
            if result.returncode == 0 and path.exists():
                return True
        if shutil.which("gnome-screenshot"):
            result = subprocess.run(["gnome-screenshot", "-f", str(path)], capture_output=True)
            if result.returncode == 0 and path.exists():
                return True
        if shutil.which("grim"):  # Wayland
            result = subprocess.run(["grim", str(path)], capture_output=True)
            if result.returncode == 0 and path.exists():
                return True

    # Cross-platform fallback
    try:
        import pyautogui
        img = pyautogui.screenshot()
        img.save(str(path))
        return path.exists()
    except Exception:
        pass
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(str(path))
        return path.exists()
    except Exception:
        return False


# ── Volume ────────────────────────────────────────────────────────────────────

def set_volume(level: int) -> dict:
    """Set system volume (0–100)."""
    level = max(0, min(100, int(level)))

    if IS_MAC:
        subprocess.run(["osascript", "-e", f"set volume output volume {level}"])
        return {"success": True, "message": f"Volume set to {level}%"}

    if IS_LINUX:
        if shutil.which("pactl"):
            result = subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return {"success": True, "message": f"Volume set to {level}%"}
            return {"success": False, "message": result.stderr.strip() or "pactl failed"}
        if shutil.which("amixer"):
            result = subprocess.run(
                ["amixer", "-D", "pulse", "sset", "Master", f"{level}%"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return {"success": True, "message": f"Volume set to {level}%"}
            return {"success": False, "message": result.stderr.strip() or "amixer failed"}
        return {"success": False, "message": "No volume control found (install pactl or amixer)"}

    return {"success": False, "message": f"set_volume not supported on {OS_NAME}"}


# ── Frontmost window / URL verification ───────────────────────────────────────

def get_frontmost_app() -> str:
    """Return the name of the currently focused application / window."""
    if IS_MAC:
        script = (
            'tell application "System Events" to get name of first '
            "application process whose frontmost is true"
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    if IS_LINUX:
        if shutil.which("xprop"):
            try:
                root = subprocess.run(
                    ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                    capture_output=True, text=True,
                )
                m = re.search(r"0x[0-9a-fA-F]+", root.stdout or "")
                if m:
                    wid = m.group(0)
                    info = subprocess.run(
                        ["xprop", "-id", wid, "WM_CLASS", "WM_NAME"],
                        capture_output=True, text=True,
                    )
                    # Prefer WM_CLASS instance/class, fall back to window title
                    for line in info.stdout.splitlines():
                        if line.startswith("WM_CLASS"):
                            classes = re.findall(r'"([^"]+)"', line)
                            if classes:
                                return classes[-1]  # usually the class name
                    for line in info.stdout.splitlines():
                        if "WM_NAME" in line and "=" in line:
                            title = line.split("=", 1)[1].strip().strip('"')
                            if title:
                                return title
            except Exception:
                pass
        if shutil.which("xdotool"):
            wid = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True,
            )
            if wid.returncode == 0 and wid.stdout.strip():
                name = subprocess.run(
                    ["xdotool", "getwindowname", wid.stdout.strip()],
                    capture_output=True, text=True,
                )
                if name.returncode == 0:
                    return name.stdout.strip() or "unknown"
        if shutil.which("wmctrl"):
            result = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True)
            lines = [l for l in result.stdout.splitlines() if l.strip()]
            if lines:
                parts = lines[-1].split(None, 4)
                if len(parts) >= 5:
                    return parts[4]
        return "unknown"

    return "unknown"


def get_active_url() -> str:
    """Best-effort: return the URL from the active browser tab (macOS AppleScript only)."""
    if IS_MAC:
        script = '''
        tell application "Google Chrome"
            if (count of windows) > 0 then
                return URL of active tab of front window
            end if
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else ""
    # Linux: no reliable cross-browser API without extensions — skip
    return ""
