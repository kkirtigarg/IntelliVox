"""
agent/computer_use.py
General-purpose computer control: see screen → decide → act → repeat.

Uses a vision LLM (Ollama) + pyautogui to perform arbitrary UI tasks like a human:
click links, play videos, fill forms, navigate apps, etc.
"""
from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger("intellivox.computer_use")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC     = platform.system() == "Darwin"
MODIFIER   = "cmd" if IS_MAC else "ctrl"
PASTE_KEYS = ("command", "v") if IS_MAC else ("ctrl", "v")

# Vision model — must support images in Ollama (e.g. llama3.2-vision, moondream)
VISION_MODEL = "llama3.2-vision"
MAX_STEPS = 20
ACTION_DELAY = 0.8  # seconds after each action for UI to update

SYSTEM_PROMPT = f"""You are a computer-use agent controlling a {"macOS" if IS_MAC else "Windows"} desktop.
You receive screenshots and must complete the user's goal step by step.

Respond with ONLY valid JSON (no markdown fences):
{{
  "thought": "brief reasoning about what you see and what to do next",
  "action": "click|double_click|type|press|scroll|wait|done|fail",
  "args": {{}},
  "done": false
}}

Actions and args:
- click:       {{"x": <int>, "y": <int>}}           — pixel coordinates on screen
- double_click:{{"x": <int>, "y": <int>}}
- type:        {{"text": "<string>"}}                 — types into focused field
- press:       {{"key": "enter"|"space"|"tab"|"{MODIFIER}+l"|"{MODIFIER}+t"|"esc"|...}}
- scroll:      {{"clicks": <int>}}                   — positive=up, negative=down
- wait:        {{"seconds": <float>}}                 — wait for page/load
- done:        {{"message": "<what was accomplished>"}} — goal complete, set done=true
- fail:        {{"message": "<why impossible>"}}      — cannot continue, set done=true

Rules:
1. Use ONLY coordinates visible in the screenshot. Screen size is given each turn.
2. One action per turn. Observe the result in the next screenshot before continuing.
3. For web tasks: click search boxes, type queries, press enter, click first result, click play.
4. For YouTube: search → click first video thumbnail → video usually autoplays; or click play button.
5. If a browser address bar is needed, press {MODIFIER}+l then type URL/query.
6. Set done=true only when the user's goal is clearly achieved.
7. Prefer clicking visible buttons/links over blind keyboard shortcuts.
8. If stuck after several attempts, use action "fail" with explanation.
"""


def _screen_size() -> tuple[int, int]:
    try:
        import pyautogui
        w, h = pyautogui.size()
        return int(w), int(h)
    except Exception:
        return 1920, 1080


def _capture_screenshot() -> tuple[str | None, str | None]:
    """Return (path, error)."""
    tmp = Path(tempfile.gettempdir()) / "intellivox_cu_screen.png"

    if not IS_MAC:
        try:
            from PIL import ImageGrab
            ImageGrab.grab().save(str(tmp), "PNG")
            return str(tmp), None
        except Exception as e:
            return None, f"screenshot failed: {e}"

    result = subprocess.run(
        ["screencapture", "-x", "-t", "png", str(tmp)],
        capture_output=True,
    )
    if result.returncode != 0 or not tmp.exists():
        return None, "screencapture failed — grant Screen Recording permission"
    return str(tmp), None


def _execute_action(action: str, args: dict) -> dict:
    """Run one physical action on the desktop."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
    except ImportError:
        return {"success": False, "message": "pyautogui not installed"}

    action = (action or "").lower().strip()
    args = args or {}

    try:
        if action == "click":
            x, y = int(args["x"]), int(args["y"])
            pyautogui.click(x, y)
            return {"success": True, "message": f"Clicked ({x}, {y})"}

        if action == "double_click":
            x, y = int(args["x"]), int(args["y"])
            pyautogui.doubleClick(x, y)
            return {"success": True, "message": f"Double-clicked ({x}, {y})"}

        if action == "type":
            text = str(args.get("text", ""))
            try:
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey(*PASTE_KEYS)
            except Exception:
                pyautogui.write(text, interval=0.03)
            return {"success": True, "message": f"Typed {len(text)} chars"}

        if action == "press":
            key = str(args.get("key", "enter")).lower()
            keys = [k.strip() for k in key.split("+")]
            if not IS_MAC:
                keys = ["ctrl" if k == "cmd" else k for k in keys]
            if len(keys) == 1:
                pyautogui.press(keys[0])
            else:
                pyautogui.hotkey(*keys)
            return {"success": True, "message": f"Pressed {key}"}

        if action == "scroll":
            clicks = int(args.get("clicks", -3))
            pyautogui.scroll(clicks)
            return {"success": True, "message": f"Scrolled {clicks}"}

        if action == "wait":
            secs = float(args.get("seconds", 1.0))
            time.sleep(min(secs, 5.0))
            return {"success": True, "message": f"Waited {secs}s"}

        if action in ("done", "fail"):
            return {"success": action == "done", "message": args.get("message", action)}

        return {"success": False, "message": f"Unknown action: {action}"}

    except Exception as e:
        return {"success": False, "message": str(e)}


def _parse_decision(raw: str) -> dict | None:
    """Extract JSON decision from model output."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _ask_vision(image_path: str, goal: str, history: list[str], step: int, width: int, height: int) -> dict | None:
    import ollama

    history_text = "\n".join(f"  - {h}" for h in history[-8:]) or "  (none yet)"
    user_msg = (
        f"Goal: {goal}\n"
        f"Screen size: {width} x {height} pixels\n"
        f"Step: {step + 1} of {MAX_STEPS}\n"
        f"Previous actions:\n{history_text}\n\n"
        f"What is the next action? Return JSON only."
    )

    try:
        response = ollama.chat(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg, "images": [image_path]},
            ],
            options={"temperature": 0.1},
        )
        raw = response["message"]["content"]
        log.debug("Vision raw: %s", raw[:300])
        return _parse_decision(raw)
    except Exception as e:
        log.error("Vision model error: %s", e)
        return None


def run_computer_task(goal: str, max_steps: int = MAX_STEPS) -> dict:
    """
    Autonomous loop: screenshot → vision LLM → action → repeat.
    Returns { success, message, steps, log }.
    """
    if not goal or not goal.strip():
        return {"success": False, "message": "No goal provided", "steps": 0, "log": []}

    width, height = _screen_size()
    history: list[str] = []
    step_log: list[dict] = []

    log.info("Computer use task: %r", goal)

    for step in range(max_steps):
        image_path, err = _capture_screenshot()
        if err:
            return {"success": False, "message": err, "steps": step, "log": step_log}

        decision = _ask_vision(image_path, goal, history, step, width, height)
        if not decision:
            history.append("error: could not parse model response")
            step_log.append({"step": step + 1, "error": "parse failed"})
            continue

        thought = decision.get("thought", "")
        action = decision.get("action", "")
        args = decision.get("args") or {}
        done = decision.get("done", False)

        log.info("Step %d: %s → %s(%s)", step + 1, thought[:80], action, args)

        if action in ("done", "fail") or done:
            msg = args.get("message") or thought or ("Task completed" if action == "done" else "Task failed")
            step_log.append({"step": step + 1, "thought": thought, "action": action, "message": msg})
            return {
                "success": action == "done" or (done and action != "fail"),
                "message": msg,
                "steps": step + 1,
                "log": step_log,
            }

        result = _execute_action(action, args)
        entry = {"step": step + 1, "thought": thought, "action": action, "args": args, "result": result.get("message")}
        step_log.append(entry)
        history.append(f"{action}({args}) → {result.get('message', 'ok')}")

        if not result.get("success"):
            history.append(f"action failed: {result.get('message')}")

        time.sleep(ACTION_DELAY)

    return {
        "success": False,
        "message": f"Reached max steps ({max_steps}) without completing the goal.",
        "steps": max_steps,
        "log": step_log,
    }


def set_vision_model(name: str) -> None:
    global VISION_MODEL
    VISION_MODEL = name
    log.info("Vision model: %s", name)
