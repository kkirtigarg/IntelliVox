"""
agent/planner.py
LLM-based planner: converts a voice transcript into a structured action plan.
Key improvements:
  - Injects real system context (username, home dir, installed apps)
  - Knows to use find_file (Spotlight) instead of guessing paths
  - Understands macOS app names (no Windows equivalents)
  - Supports chaining: previous step results feed into next plan
  - Never guesses file paths
"""
import json
import os
import re
import logging
import subprocess
from pathlib import Path

import ollama

log = logging.getLogger("intellivox.planner")

MODEL = "llama3.1"

# ── Gather real system context ─────────────────────────────────────────────────

HOME     = str(Path.home())
USERNAME = Path.home().name


def _get_installed_apps() -> list[str]:
    """List installed .app bundles from /Applications."""
    try:
        apps = [
            p.stem for p in Path("/Applications").iterdir()
            if p.suffix == ".app"
        ]
        return sorted(apps)[:40]  # limit for prompt size
    except Exception:
        return []


def _get_desktop_files() -> list[str]:
    """List files on the Desktop."""
    try:
        return sorted(os.listdir(os.path.join(HOME, "Desktop")))[:20]
    except Exception:
        return []


def _build_system_prompt() -> str:
    installed_apps = _get_installed_apps()
    desktop_files  = _get_desktop_files()

    return f"""You are IntelliVox, an AI desktop assistant for macOS.
You convert spoken instructions into precise tool calls.

═══════════════════════════════════════════════════
SYSTEM CONTEXT (real values — use these exactly)
═══════════════════════════════════════════════════
Username   : {USERNAME}
Home dir   : {HOME}
Desktop    : {HOME}/Desktop
Documents  : {HOME}/Documents
Downloads  : {HOME}/Downloads
OS         : macOS (not Windows — no .exe, no notepad.exe, no cmd)

Desktop files (sample):
{chr(10).join("  - " + f for f in desktop_files[:15])}

Installed apps (sample):
{chr(10).join("  - " + a for a in installed_apps[:30])}

═══════════════════════════════════════════════════
CRITICAL RULES
═══════════════════════════════════════════════════
1. NEVER guess file paths. If you need to open/find a file and don't know
   the exact full path, ALWAYS use find_file first, then use the path from
   its result in the next step (open_file).

2. macOS app names only:
   - "notepad"    → "TextEdit"
   - "calculator" → "Calculator"
   - "explorer"   → "Finder"
   - "numbers"    → "Numbers"  (use open_app, NOT google_search)
   - "pages"      → "Pages"
   - "keynote"    → "Keynote"
   - "excel"      → "Microsoft Excel" or "Numbers"
   - "word"       → "Microsoft Word"  or "Pages"

3. Use the REAL username "{USERNAME}" in any paths you construct.

4. For chained tasks like "open Finder and open the Talent Hack PDF":
   Step 1: open_app("Finder")
   Step 2: find_file("Talent Hack")       ← Spotlight search
   Step 3: open_file(path from step 2)    ← use {{{{step_2_result.path}}}}

5. Path placeholders: when a step depends on a previous result, use:
   {{{{step_N_result.field}}}}  e.g. {{{{step_2_result.path}}}}

6. Never use google_search or navigate_url for apps that exist on this Mac.

7. If you cannot satisfy the request safely, set clarification_needed=true.

═══════════════════════════════════════════════════
AVAILABLE TOOLS
═══════════════════════════════════════════════════
BROWSER:
  open_browser(browser="chrome"|"firefox"|"safari")
  navigate_url(url: str, browser="chrome")
  google_search(query: str, browser="chrome")
  youtube_search(query: str, browser="chrome")

DESKTOP:
  open_app(name: str)          ← macOS app name e.g. "Finder", "Numbers"
  close_app(name: str)
  type_text(text: str)
  press_key(key: str)          ← e.g. "cmd+t", "enter"
  set_volume(level: int)       ← 0-100

FILES:
  find_file(name: str)         ← Spotlight search, returns path — USE THIS first
  list_files(directory: str)   ← lists files in a directory
  open_file(path: str)         ← opens file with default app
  read_file(path: str)         ← reads plain text file content
  write_file(path: str, content: str)
  delete_file(path: str)       ← DESTRUCTIVE, needs confirmation
  move_file(src: str, dst: str)

DOCUMENTS & AI:
  read_pdf(path: str)          ← extracts all text from a PDF, returns .text field
  summarize(text: str, style="concise"|"detailed"|"bullets")
                               ← summarizes text using local LLM
  summarize_codebase(directory: str, style="bullets")
                               ← reads source files in a folder and summarizes the project
  answer_question(text: str, question: str)
                               ← answers a question about document content

COMPUTER USE (general — prefer for interactive tasks):
  computer_use(goal: str)      ← sees the screen, clicks/types like a human until done.
                               USE THIS for play song, click links, forms, any UI task.

8. For "summarize code / go through folder / explain project":
   Do NOT open Finder unless user explicitly asks to open it.
   Step 1: find_file(name="<folder name>")
   Step 2: summarize_codebase(directory={{{{step_1_result.path}}}}, style="bullets")

9. GENERAL RULE — use computer_use for ANY task requiring on-screen interaction
   like a human: clicking links, playing videos, forms, buttons, menus.
   Do NOT chain open_browser + navigate_url + press_key for these.
   Use ONE step: computer_use(goal="<full user instruction>")
   Keep open_app / navigate_url / find_file ONLY for simple single actions
   with no further UI interaction (e.g. "open Finder", "go to google.com").

═══════════════════════════════════════════════════
CHAINING EXAMPLES
═══════════════════════════════════════════════════
"summarize the Talent Hack PDF":
  Step 1: find_file(name="Talent Hack")
  Step 2: read_pdf(path={{step_1_result.path}})
  Step 3: summarize(text={{step_2_result.text}}, style="bullets")

"what does the PDF say about pricing?":
  Step 1: find_file(name="<pdf name>")
  Step 2: read_pdf(path={{step_1_result.path}})
  Step 3: answer_question(text={{step_2_result.text}}, question="what does it say about pricing?")

"open finder and open the Talent Hack PDF":
  Step 1: open_app(name="Finder")
  Step 2: find_file(name="Talent Hack")
  Step 3: open_file(path={{step_2_result.path}})

"go through IntelliVox folder and summarize the code":
  Step 1: find_file(name="IntelliVox")
  Step 2: summarize_codebase(directory={{{{step_1_result.path}}}}, style="bullets")

"search YouTube for a song and play it":
  Step 1: computer_use(goal="Open Chrome, search YouTube for <song name>, click the first video, and play it")

"go to first link and play the song":
  Step 1: computer_use(goal="Click the first link on the current page and play the song/video")

═══════════════════════════════════════════════════
RESPONSE FORMAT (strict JSON, no markdown fences)
═══════════════════════════════════════════════════
{{
  "intent": "short label",
  "explanation": "one sentence of what you will do",
  "steps": [
    {{ "tool": "tool_name", "args": {{ "key": "value" }} }}
  ],
  "clarification_needed": false,
  "clarification_question": null
}}

Output ONLY valid JSON. Nothing else."""


def _resolve_placeholders(args: dict, step_results: list[dict]) -> dict:
    """
    Replace {{step_N_result.field}} placeholders with actual values
    from previous step results. Enables chaining.
    """
    args_str = json.dumps(args)
    changed  = False

    pattern = re.compile(r'\{\{step_(\d+)_result\.(\w+)\}\}')
    for match in pattern.finditer(args_str):
        step_idx = int(match.group(1)) - 1  # 1-indexed in prompt, 0-indexed here
        field    = match.group(2)
        if 0 <= step_idx < len(step_results):
            value = step_results[step_idx].get(field, "")
            if value:
                args_str = args_str.replace(match.group(0), str(value))
                changed = True

    return json.loads(args_str) if changed else args


def plan(transcript: str, previous_context: str = "") -> dict:
    """
    Convert a voice transcript into a structured action plan.
    previous_context: summary of what has happened in this conversation so far.
    """
    log.info("Planning: %r", transcript)

    system_prompt = _build_system_prompt()

    user_msg = f'User said: "{transcript}"'
    if previous_context:
        user_msg += f'\n\nContext from previous steps:\n{previous_context}'

    try:
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            options={"temperature": 0.05},  # very low for consistent structured output
        )
        raw = response["message"]["content"].strip()
        log.debug("LLM raw: %s", raw)

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$",          "", raw)

        # Extract JSON object
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            raw = json_match.group(0)

        result = json.loads(raw)
        log.info("Plan: [%s] %d steps — %s",
                 result.get("intent"), len(result.get("steps", [])),
                 result.get("explanation"))
        return result

    except json.JSONDecodeError as e:
        log.error("LLM returned invalid JSON: %s", e)
        return _fallback_plan(transcript)
    except Exception as e:
        log.error("Planner error: %s", e)
        return _fallback_plan(transcript)


def resolve_step_args(args: dict, step_results: list[dict]) -> dict:
    """Public wrapper — resolves placeholders in step args using prior results."""
    return _resolve_placeholders(args, step_results)


def _fallback_plan(transcript: str) -> dict:
    return {
        "intent":                "unknown",
        "explanation":           "Could not understand the instruction.",
        "steps":                 [],
        "clarification_needed":  True,
        "clarification_question": f"I couldn't fully understand '{transcript}'. Could you rephrase it?",
    }


def set_model(model_name: str):
    global MODEL
    MODEL = model_name
    log.info("LLM model: %s", model_name)
