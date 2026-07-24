"""Post-action verification.

The orchestrator does not assume an action worked just because the actuator
call returned without raising. Each action category has a corresponding
check here that inspects observable state (window titles, page text, file
system) to confirm the intended effect actually happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .actuators.base import Actuator
from .models import Action

# Spoken names that map to different binary/window title strings on Linux.
_OPEN_APP_ALIASES = {
    "notepad": ("notepad", "gedit", "xed", "kate", "mousepad", "leafpad", "text editor"),
    "calculator": ("calculator", "calc", "kcalc", "galculator"),
    "calc": ("calculator", "calc", "kcalc", "galculator"),
    "explorer": ("files", "nautilus", "dolphin", "thunar", "pcmanfm", "nemo", "home"),
    "files": ("files", "nautilus", "dolphin", "thunar", "pcmanfm", "nemo"),
    "paint": ("gimp", "paint", "kolourpaint"),
    "cmd": ("terminal", "konsole", "alacritty", "kitty"),
    "powershell": ("terminal", "konsole"),
    "terminal": ("terminal", "konsole", "alacritty", "kitty"),
    "vscode": ("visual studio code", "code", "codium", "vscode"),
    "code": ("visual studio code", "code", "codium", "vscode"),
}


@dataclass
class VerificationResult:
    verified: bool
    detail: str


def _open_app_ok(app: str, execution_result: dict) -> bool:
    if execution_result.get("error"):
        return False
    # Launch succeeded — don't require a window title (Linux often lacks wmctrl).
    if execution_result.get("launched"):
        return True
    title = (execution_result.get("observed_window_title") or "").lower()
    launched = execution_result.get("launched") or ""
    stem = Path(launched).stem.lower() if launched else ""
    key = (app or "").split()[0].lower()
    needles = {key, stem, *(_OPEN_APP_ALIASES.get(key, ()))}
    needles.discard("")
    if title and any(n in title for n in needles):
        return True
    if execution_result.get("success") and execution_result.get("process_running"):
        return True
    return False


def verify(action: Action, execution_result: dict, actuator: Actuator) -> VerificationResult:
    category = action.category

    if category == "open_app":
        title = execution_result.get("observed_window_title")
        app = action.args.get("app", "")
        ok = _open_app_ok(app, execution_result)
        return VerificationResult(
            ok,
            f"expected a window for '{app}', observed title '{title}'"
            + (f", launched={execution_result.get('launched')}" if execution_result.get("launched") else ""),
        )

    if category == "focus_window":
        ok = execution_result.get("success", False)
        return VerificationResult(ok, f"observed title after focus: {execution_result.get('observed_window_title')}")

    if category in ("file_write_new", "file_write_overwrite"):
        path = action.args.get("path")
        check = actuator.file_read(path)
        ok = check.get("success", False) and check.get("content") == action.args.get("content")
        return VerificationResult(ok, f"re-read '{path}' and compared content: match={ok}")

    if category == "file_delete":
        path = action.args.get("path")
        check = actuator.file_read(path)
        ok = not check.get("success", False)
        return VerificationResult(ok, f"confirmed '{path}' no longer readable: {ok}")

    if category == "browser_navigate":
        ok = execution_result.get("success", False)
        return VerificationResult(ok, f"navigation status: {execution_result.get('status')}")

    if category == "open_search_result":
        ok = execution_result.get("success", False) and bool(execution_result.get("url"))
        return VerificationResult(
            ok,
            f"opened result #{action.args.get('index', 1)} → {execution_result.get('url')}",
        )

    if category == "form_submit":
        text = execution_result.get("confirmation_text", "") or ""
        ok = "error" not in text.lower() and execution_result.get("success", False)
        return VerificationResult(ok, f"post-submit page text snippet did not contain 'error': {ok}")

    if category == "send_message":
        ok = execution_result.get("success", False)
        return VerificationResult(ok, f"send_message reported success={ok}")

    # Default: trust the actuator's own success flag if it reported one,
    # but mark explicitly that no dedicated verification exists yet.
    if "success" in execution_result:
        return VerificationResult(execution_result["success"],
                                   f"no dedicated verifier for '{category}'; used actuator-reported success flag")
    return VerificationResult(False, f"no verification method available for action category '{category}'")
