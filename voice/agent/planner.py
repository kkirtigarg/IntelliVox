"""
agent/planner.py
LLM-based planner: converts a voice transcript into a structured action plan.
Key improvements:
  - Injects real system context (username, home dir, installed apps)
  - Knows to use find_file instead of guessing paths
  - Understands platform-native app names (macOS / Linux)
  - Supports chaining: previous step results feed into next plan
  - Never guesses file paths
"""
import json
import os
import re
import logging
from pathlib import Path

import ollama

from agent import platform as plat

log = logging.getLogger("intellivox.planner")

MODEL = "llama3.1"

# ── Gather real system context ─────────────────────────────────────────────────

HOME     = plat.HOME
USERNAME = Path.home().name
_MOD     = plat.MOD_KEY


def _get_installed_apps() -> list[str]:
    return plat.list_installed_apps(40)


def _get_desktop_files() -> list[str]:
    """List files on the Desktop."""
    try:
        return sorted(os.listdir(os.path.join(HOME, "Desktop")))[:20]
    except Exception:
        return []


def _app_name_rules() -> str:
    if plat.IS_LINUX:
        return f"""2. Linux app names (use these aliases with open_app):
   - "notepad" / "textedit" → "notepad" (opens mousepad/gedit/kate)
   - "calculator"           → "calculator"
   - "explorer" / "finder"  → "finder" (opens thunar/nautilus/dolphin)
   - "terminal"             → "terminal"
   - "chrome" / "browser"   → "chrome" or "firefox" / "brave"
   - "word" / "excel"       → "libreoffice"
   - Prefer names from the installed apps list below.
   Keyboard shortcuts use {_MOD}+key (e.g. "{_MOD}+t"), NOT cmd."""
    return f"""2. macOS app names only:
   - "notepad"    → "TextEdit"
   - "calculator" → "Calculator"
   - "explorer"   → "Finder"
   - "numbers"    → "Numbers"  (use open_app, NOT google_search)
   - "pages"      → "Pages"
   - "keynote"    → "Keynote"
   - "excel"      → "Microsoft Excel" or "Numbers"
   - "word"       → "Microsoft Word"  or "Pages"
   Keyboard shortcuts use {_MOD}+key (e.g. "{_MOD}+t")."""


def _build_system_prompt() -> str:
    installed_apps = _get_installed_apps()
    desktop_files  = _get_desktop_files()
    file_manager   = "Finder" if plat.IS_MAC else "finder"
    search_label   = "Spotlight" if plat.IS_MAC else "file search"

    return f"""You are IntelliVox, an AI desktop assistant for {plat.OS_NAME}.
You convert spoken instructions into precise tool calls.

═══════════════════════════════════════════════════
SYSTEM CONTEXT (real values — use these exactly)
═══════════════════════════════════════════════════
Username   : {USERNAME}
Home dir   : {HOME}
Desktop    : {HOME}/Desktop
Documents  : {HOME}/Documents
Downloads  : {HOME}/Downloads
OS         : {plat.OS_NAME}

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

{_app_name_rules()}

3. Use the REAL username "{USERNAME}" in any paths you construct.

4. For chained tasks like "open {file_manager} and open the Talent Hack PDF":
   Step 1: open_app("{file_manager}")
   Step 2: find_file("Talent Hack")       ← {search_label}
   Step 3: open_file(path from step 2)    ← use {{{{step_2_result.path}}}}

5. Path placeholders: when a step depends on a previous result, use:
   {{{{step_N_result.field}}}}  e.g. {{{{step_2_result.path}}}}

6. Never use google_search or navigate_url for apps that exist on this machine.

7. If you cannot satisfy the request safely, set clarification_needed=true.

═══════════════════════════════════════════════════
AVAILABLE TOOLS
═══════════════════════════════════════════════════
BROWSER:
  open_browser(browser="chrome"|"firefox"|"brave")
  navigate_url(url: str, browser="chrome")
  google_search(query: str, browser="chrome")
  youtube_search(query: str, browser="chrome")

DESKTOP:
  open_app(name: str)          ← platform app/alias e.g. "{file_manager}", "firefox"
  close_app(name: str)
  type_text(text: str)
  press_key(key: str)          ← e.g. "{_MOD}+t", "enter"
  set_volume(level: int)       ← 0-100

FILES:
  find_file(name: str)         ← {search_label}, returns path — USE THIS first
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
  create_dummy_file(path="", content="")
                               ← creates a dummy baseline document for comparison
  compare_documents(text_a, text_b, label_a="Instance A", label_b="Instance B", style="bullets")
                               ← compares exactly TWO text instances and returns a comparison summary
  compare_pdf_with_dummy(pdf_path, dummy_path="", style="bullets")
                               ← reads PDF → saves its summary as one instance, creates dummy as the other,
                                 then compares both and returns a comparison summary
                               PREFER this for "compare PDF with dummy / compare two instances" requests

COMPUTER USE (general — prefer for interactive tasks):
  computer_use(goal: str)      ← sees the screen, clicks/types like a human until done.
                               USE THIS for play song, click links, forms, any UI task.

8. For "summarize code / go through folder / explain project":
   Do NOT open the file manager unless user explicitly asks to open it.
   Step 1: find_file(name="<folder name>")
   Step 2: summarize_codebase(directory={{{{step_1_result.path}}}}, style="bullets")

9. GENERAL RULE — use computer_use for ANY task requiring on-screen interaction
   like a human: clicking links, playing videos, forms, buttons, menus.
   Do NOT chain open_browser + navigate_url + press_key for these.
   Use ONE step: computer_use(goal="<full user instruction>")
   Keep open_app / navigate_url / find_file ONLY for simple single actions
   with no further UI interaction (e.g. "open {file_manager}", "go to google.com").

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

"compare the PDF with a dummy document" / "compare two instances and summarize":
  Step 1: find_file(name="<pdf name>")
  Step 2: compare_pdf_with_dummy(pdf_path={{step_1_result.path}}, style="bullets")
  ← this creates: (A) PDF summary file  (B) dummy file, then compares them and returns summary

"compare these two texts side by side":
  Step 1: compare_documents(text_a="<first>", text_b="<second>", style="bullets")

"open file manager and open the Talent Hack PDF":
  Step 1: open_app(name="{file_manager}")
  Step 2: find_file(name="Talent Hack")
  Step 3: open_file(path={{step_2_result.path}})

"go through IntelliVox folder and summarize the code":
  Step 1: find_file(name="IntelliVox")
  Step 2: summarize_codebase(directory={{{{step_1_result.path}}}}, style="bullets")

"search YouTube for a song and play it":
  Step 1: computer_use(goal="Open browser, search YouTube for <song name>, click the first video, and play it")

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


def _extract_json(raw: str) -> dict | None:
    """Best-effort parse of a JSON object from model output."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _heuristic_plan(transcript: str) -> dict | None:
    """
    Deterministic plans for common short voice commands.
    Used when the LLM returns empty/invalid JSON (e.g. Ollama GPU crash).
    """
    t = transcript.strip()
    low = t.lower().strip(" .,!?")

    # open <app>
    m = re.match(
        r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+)?(.+?)(?:\s+app(?:lication)?)?$",
        low,
    )
    if m:
        name = m.group(1).strip()
        # drop filler words
        name = re.sub(r"\b(the|a|an|app|application|please)\b", "", name).strip()
        if name:
            return {
                "intent": "open_app",
                "explanation": f"Opening {name}.",
                "steps": [{"tool": "open_app", "args": {"name": name}}],
                "clarification_needed": False,
                "clarification_question": None,
            }

    # close <app>
    m = re.match(
        r"^(?:please\s+)?(?:close|quit|exit|kill)\s+(?:the\s+)?(.+?)(?:\s+app(?:lication)?)?$",
        low,
    )
    if m:
        name = re.sub(r"\b(the|a|an|app|application|please)\b", "", m.group(1)).strip()
        if name:
            return {
                "intent": "close_app",
                "explanation": f"Closing {name}.",
                "steps": [{"tool": "close_app", "args": {"name": name}}],
                "clarification_needed": False,
                "clarification_question": None,
            }

    # google / search
    m = re.match(r"^(?:google|search(?:\s+google)?(?:\s+for)?)\s+(.+)$", low)
    if m:
        query = m.group(1).strip()
        return {
            "intent": "google_search",
            "explanation": f"Searching Google for {query}.",
            "steps": [{"tool": "google_search", "args": {"query": query}}],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # youtube
    m = re.match(r"^(?:youtube|search\s+youtube(?:\s+for)?)\s+(.+)$", low)
    if m:
        query = m.group(1).strip()
        return {
            "intent": "youtube_search",
            "explanation": f"Searching YouTube for {query}.",
            "steps": [{"tool": "youtube_search", "args": {"query": query}}],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # go to / open url
    m = re.match(r"^(?:go\s+to|open\s+url|navigate\s+to)\s+(\S+)$", low)
    if m:
        url = m.group(1).strip()
        return {
            "intent": "navigate_url",
            "explanation": f"Opening {url}.",
            "steps": [{"tool": "navigate_url", "args": {"url": url}}],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # volume
    m = re.match(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?$", low)
    if m:
        level = int(m.group(1))
        return {
            "intent": "set_volume",
            "explanation": f"Setting volume to {level}%.",
            "steps": [{"tool": "set_volume", "args": {"level": level}}],
            "clarification_needed": False,
            "clarification_question": None,
        }

    return None


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
    user_msg += "\n\nRespond with ONLY a valid JSON object matching the schema. No prose."

    try:
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            format="json",
            options={"temperature": 0.05},
        )
        msg = response.get("message") if isinstance(response, dict) else response["message"]
        raw = (msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)) or ""
        raw = str(raw).strip()
        log.debug("LLM raw: %s", raw[:500] if raw else "<empty>")

        if not raw:
            raise ValueError(
                "Ollama returned empty content (often a GPU/Vulkan crash). "
                "Fix: restart ollama with AMD-only Vulkan — see voice/fix-ollama-gpu.sh"
            )

        result = _extract_json(raw)
        if not result:
            raise json.JSONDecodeError("No JSON object in model output", raw, 0)

        log.info("Plan: [%s] %d steps — %s",
                 result.get("intent"), len(result.get("steps", [])),
                 result.get("explanation"))
        return result

    except Exception as e:
        log.error("Planner LLM failed: %s", e)
        heuristic = _heuristic_plan(transcript)
        if heuristic:
            log.info("Using heuristic plan: %s", heuristic.get("intent"))
            return heuristic
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
