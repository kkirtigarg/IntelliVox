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

from agent.telemetry import track
from agent.plan_validator import validate_plan

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
Language   : English only — user speaks English; interpret all instructions in English.

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
  youtube_play(query: str, browser="chrome")   ← finds top result and plays in Chrome

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
  save_summary_file(summary: str, filename: str, directory="~/Desktop")
                               ← saves summary text to a .txt file
  answer_question(text: str, question: str)
                               ← answers a question about document content

MAIL (Gmail in Google Chrome — uses your Chrome login):
  open_gmail(query: str, browser="chrome", recent=True)
  read_gmail(limit=5, wait=5, browser="chrome")
  read_gmail returns .body with top inbox rows scraped from the Gmail tab

WEB (Playwright — prefer over computer_use for web):
  web_browse(goal: str, url="") ← opens page and returns visible text

COMPUTER USE (last resort — vision is slow and unreliable):
  computer_use(goal: str)      ← only when no browser/file tool can do the job.

8. For "summarize code / go through folder / explain project":
   Do NOT open Finder unless user explicitly asks to open it.
   Step 1: find_file(name="<folder name>")
   Step 2: summarize_codebase(directory={{{{step_1_result.path}}}}, style="bullets")
   If user also asks to save/export/write to a txt file:
   Step 3: save_summary_file(summary={{{{step_2_result.summary}}}}, filename="<folder>-summary.txt", directory="~/Desktop")

9. YOUTUBE / WEB SEARCH — ALWAYS use browser tools (NOT computer_use):
   - "search YouTube for X" → youtube_search(query="...", browser="chrome")
   - "search YouTube for X and play it" → youtube_play(query="...", browser="chrome")
   - youtube_play opens Chrome on the top video URL and starts playback.
   - Do NOT use computer_use for YouTube unless user says "click the third result" etc.

10. Use computer_use ONLY for tasks that cannot be done with a single tool call
    (e.g. fill a multi-field form, click a specific button with no URL).
    NEVER use computer_use for: open Chrome, YouTube search, Google search, open URL.

11. MAIL — use open_gmail + read_gmail + summarize in Chrome (NOT Mail.app, NOT computer_use):
   Step 1: open_gmail(browser="chrome", recent=True)  ← opens Gmail inbox in Chrome
   Step 2: read_gmail(limit=5, wait=5)               ← top 5 inbox messages
   Step 3: summarize(text={{{{step_2_result.body}}}}, style="bullets")

12. WEB tasks in browser — prefer web_browse over computer_use when possible.

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

"open Chrome, search YouTube for Shakira song and play it":
  Step 1: youtube_play(query="Shakira song", browser="chrome")

"search YouTube for a song and play it":
  Step 1: youtube_play(query="<song name>", browser="chrome")

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


def _extract_youtube_query(transcript: str) -> str | None:
    """Pull a search query from common YouTube voice phrasings."""
    text = transcript.strip()
    lower = text.lower()

    patterns = [
        r"(?:search(?:\s+(?:for|on))?\s+)?youtube(?:\s+for|\s+search(?:\s+for)?)\s+(.+?)(?:\s+and\s+play|\s+play\s+it|\.)*$",
        r"search\s+youtube\s+(?:for\s+)?(.+?)(?:\s+and\s+play|\s+play\s+it|\.)*$",
        r"(?:search(?:\s+for)?\s+)(.+?)\s+(?:on\s+)?youtube(?:\s+and\s+play|\s+play\s+it|\.)*$",
        r"youtube(?:\s+for|\s+search(?:\s+for)?)\s+(.+?)(?:\s+and\s+play|\s+play\s+it|\.)*$",
        r"(?:play(?:\s+the)?\s+)(.+?)(?:\s+(?:song|video))?(?:\s+on\s+youtube|\s+from\s+youtube|$)",
    ]
    for pat in patterns:
        match = re.search(pat, lower, re.I)
        if not match:
            continue
        query = match.group(1).strip()
        query = re.sub(r"\s+(song|video|music)$", "", query, flags=re.I)
        query = re.sub(r"^the\s+", "", query, flags=re.I)
        query = re.sub(r"\s+and\s+play(\s+it)?$", "", query, flags=re.I)
        query = re.sub(r"\s+and$", "", query, flags=re.I)
        query = re.sub(r"\s+on\s+chrome$", "", query, flags=re.I)
        query = query.strip(" .")
        if len(query) >= 2:
            return query
    return None


def _try_youtube_plan(transcript: str) -> dict | None:
    """Reliable path for YouTube search — avoids flaky computer_use vision."""
    lower = transcript.lower()
    if not any(k in lower for k in ("youtube", "you tube", " you tube ")):
        return None

    query = _extract_youtube_query(transcript)
    if not query:
        return None

    wants_play = bool(re.search(r"\bplay\b", lower))

    if wants_play:
        explanation = f"Opening Chrome and playing the top YouTube result for '{query}'."
        tool = "youtube_play"
    else:
        explanation = f"Opening Chrome and searching YouTube for '{query}'."
        tool = "youtube_search"

    return validate_plan({
        "intent": tool,
        "explanation": explanation,
        "steps": [{"tool": tool, "args": {"query": query, "browser": "chrome"}}],
        "clarification_needed": False,
        "clarification_question": None,
    })


def _wants_summary_saved(transcript: str) -> bool:
    lower = transcript.lower()
    return any(
        phrase in lower
        for phrase in (
            "txt file", "text file", ".txt", "save to file", "save it to",
            "write to file", "export", "save as", "in a file", "to a file",
            "save the summary", "save summary",
        )
    )


def _summary_save_location(transcript: str, folder: str) -> tuple[str, str]:
    """Return (directory, filename) for the summary .txt file."""
    lower = transcript.lower()
    directory = os.path.join(HOME, "Desktop")
    if "documents" in lower:
        directory = os.path.join(HOME, "Documents")
    elif "downloads" in lower:
        directory = os.path.join(HOME, "Downloads")

    safe_folder = re.sub(r"[^\w\s-]", "", folder).strip().replace(" ", "-") or "project"
    filename = f"{safe_folder}-codebase-summary.txt"

    for pat in (
        r"(?:save (?:it )?(?:as|named|called))\s+([\w-]+\.txt)\b",
        r"(?:file named|named)\s+([\w-]+\.txt)\b",
    ):
        name_match = re.search(pat, lower)
        if name_match:
            filename = name_match.group(1).strip()
            break

    return directory, filename


def _extract_mail_query(transcript: str) -> str | None:
    lower = transcript.lower()
    if _wants_recent_mail(transcript):
        return None
    patterns = [
        r"(?:search(?:\s+(?:my\s+)?mail|\s+(?:my\s+)?email|\s+for))\s+(?:for\s+)?(.+?)(?:\s+and\s+(?:summarize|give|summary)|$)",
        r"(?:mail|email|inbox).*?(?:for|about|with)\s+(.+?)(?:\s+and\s+(?:summarize|summary)|$)",
        r"(?:find|look for)\s+(.+?)\s+(?:in\s+(?:my\s+)?(?:mail|email|inbox)|$)",
    ]
    for pat in patterns:
        m = re.search(pat, lower, re.I)
        if m:
            q = m.group(1).strip(" .")
            q = re.sub(r"\s+(and give me|and summarize|summary).*$", "", q, flags=re.I)
            q = re.sub(r"^(the mail|mail|email)$", "", q, flags=re.I).strip()
            if len(q) >= 2:
                return q
    if "task ticket" in lower:
        return "task ticket"
    if "new task" in lower:
        return "new task"
    return None


def _wants_recent_mail(transcript: str) -> bool:
    lower = transcript.lower()
    return any(
        phrase in lower
        for phrase in (
            "summarize the mail",
            "summarize mail",
            "summarise the mail",
            "open the mail",
            "read the mail",
            "latest mail",
            "recent mail",
            "my inbox",
            "the mail and summarize",
        )
    )


MAIL_BATCH_SIZE = 5


def _mail_read_count(transcript: str, wants_summary: bool, recent: bool) -> int:
    lower = transcript.lower()
    if re.search(r"\b(top|last|recent)\s+5\b", lower) or re.search(
        r"\bfive\s+(mail|email|message)", lower
    ):
        return MAIL_BATCH_SIZE
    if wants_summary and recent:
        return MAIL_BATCH_SIZE
    return 1


def _try_mail_plan(transcript: str) -> dict | None:
    """Gmail in Chrome: open inbox → read → optional summarize/save."""
    lower = transcript.lower()
    if not any(k in lower for k in ("mail", "email", "inbox", "gmail")):
        return None
    if not any(k in lower for k in ("search", "find", "read", "open", "summar", "summary", "ticket", "task")):
        return None

    recent = _wants_recent_mail(transcript)
    query = _extract_mail_query(transcript) or ""
    if not query and not recent:
        recent = True

    wants_summary = any(k in lower for k in ("summarize", "summary", "summarise", "overview"))
    save_to_file = _wants_summary_saved(transcript)

    read_count = _mail_read_count(transcript, wants_summary, recent)
    gmail_args: dict = {"browser": "chrome", "recent": recent}
    if query and not recent:
        gmail_args["query"] = query

    read_step_num = 2
    steps: list[dict] = [
        {"tool": "open_gmail", "args": gmail_args},
        {"tool": "read_gmail", "args": {"limit": read_count, "wait": 8, "browser": "chrome"}},
    ]
    if wants_summary:
        steps.append({
            "tool": "summarize",
            "args": {
                "text": f"{{{{step_{read_step_num}_result.body}}}}",
                "style": "bullets",
            },
        })
    if save_to_file and wants_summary:
        directory, _filename = _summary_save_location(transcript, "mail")
        steps.append({
            "tool": "save_summary_file",
            "args": {"directory": directory, "filename": "mail-summary.txt"},
        })

    if wants_summary and save_to_file:
        explanation = "Opening Gmail in Chrome, summarizing, and saving to a text file."
    elif wants_summary and recent and read_count > 1:
        explanation = f"Opening Gmail in Chrome and summarizing your top {read_count} messages."
    elif wants_summary and recent:
        explanation = "Opening Gmail in Chrome and summarizing your latest message."
    elif wants_summary and query:
        explanation = f"Searching Gmail for '{query}' in Chrome and summarizing."
    elif recent:
        explanation = "Opening Gmail inbox in Chrome."
    else:
        explanation = f"Searching Gmail for '{query}' in Chrome."

    return validate_plan({
        "intent": "gmail_search",
        "explanation": explanation,
        "steps": steps,
        "clarification_needed": False,
        "clarification_question": None,
    })


def _try_summarize_codebase_plan(transcript: str) -> dict | None:
    """Reliable 2-step plan: find folder → summarize_codebase."""
    lower = transcript.lower()
    wants_summary = any(
        k in lower for k in ("summarize", "summary", "summarise", "explain the code", "overview of")
    )
    wants_code = any(
        k in lower for k in ("code", "codebase", "code base", "project", "folder", "repository", "repo")
    )
    if not (wants_summary and wants_code):
        return None

    folder = None
    folder_patterns = [
        r"\b(intellivox)\b",
        r"(?:go through|open|find)\s+([a-z0-9][\w-]*)\s+folder",
        r"(?:on desktop we have|there is|we have)\s+(?:an?\s+)?([a-z0-9][\w\s-]*?)\s+folder",
        r"(?:folder|project)\s+(?:called|named)\s+['\"]?([a-z0-9][\w\s-]*)['\"]?",
        r"([a-z0-9][\w-]*)\s+folder",
    ]
    for pat in folder_patterns:
        match = re.search(pat, lower, re.I)
        if match:
            folder = match.group(1).strip()
            break

    if not folder:
        return None

    # Title-case common project names for Spotlight
    folder = folder.strip(" .")
    if folder.lower() == "intellivox":
        folder = "IntelliVox"

    save_to_file = _wants_summary_saved(transcript)
    steps = [
        {"tool": "find_file", "args": {"name": folder}},
        {
            "tool": "summarize_codebase",
            "args": {"directory": "{{step_1_result.path}}", "style": "bullets"},
        },
    ]
    if save_to_file:
        directory, filename = _summary_save_location(transcript, folder)
        steps.append({
            "tool": "save_summary_file",
            "args": {"directory": directory, "filename": filename},
        })

    if save_to_file:
        explanation = (
            f"Finding '{folder}', summarizing the codebase, and saving to a text file."
        )
    else:
        explanation = f"Finding '{folder}' and summarizing the codebase."

    return validate_plan({
        "intent": "summarize_codebase",
        "explanation": explanation,
        "steps": steps,
        "clarification_needed": False,
        "clarification_question": None,
    })

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


def _normalize_transcript(transcript: str) -> str:
    """Fix common speech-to-text typos before routing."""
    text = transcript
    text = re.sub(r"\bsearch\s+yutofor\b", "search youtube for", text, flags=re.I)
    text = re.sub(r"\bsearch\s+youtub\b", "search youtube for", text, flags=re.I)
    replacements = (
        (r"\byutofor\b", "youtube"),
        (r"\byutube\b", "youtube"),
        (r"\byoutub\b", "youtube"),
        (r"\byou tube\b", "youtube"),
    )
    for pat, repl in replacements:
        text = re.sub(pat, repl, text, flags=re.I)
    return text


@track(name="planner_plan", project_name="intellivox", tags=["planner"])
def plan(transcript: str, previous_context: str = "") -> dict:
    """
    Convert a voice transcript into a structured action plan.
    previous_context: summary of what has happened in this conversation so far.
    """
    transcript = _normalize_transcript(transcript)
    log.info("Planning: %r", transcript)

    youtube_plan = _try_youtube_plan(transcript)
    if youtube_plan:
        log.info("Plan: [%s] %d steps — %s (deterministic)",
                 youtube_plan.get("intent"), len(youtube_plan.get("steps", [])),
                 youtube_plan.get("explanation"))
        return youtube_plan

    codebase_plan = _try_summarize_codebase_plan(transcript)
    if codebase_plan:
        log.info("Plan: [%s] %d steps — %s (deterministic)",
                 codebase_plan.get("intent"), len(codebase_plan.get("steps", [])),
                 codebase_plan.get("explanation"))
        return codebase_plan

    mail_plan = _try_mail_plan(transcript)
    if mail_plan:
        log.info("Plan: [%s] %d steps — %s (deterministic)",
                 mail_plan.get("intent"), len(mail_plan.get("steps", [])),
                 mail_plan.get("explanation"))
        return mail_plan

    system_prompt = _build_system_prompt()

    user_msg = f'User said: "{transcript}"'
    if previous_context:
        user_msg += f'\n\nContext from previous steps:\n{previous_context}'
        user_msg += (
            "\n\nThe previous plan failed. Propose a NEW plan using ONLY tools from "
            "AVAILABLE TOOLS. Do not repeat the failed approach if a simpler tool exists."
        )

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
        return validate_plan(result)

    except json.JSONDecodeError as e:
        log.error("LLM returned invalid JSON: %s", e)
        return validate_plan(_fallback_plan(transcript))
    except Exception as e:
        log.error("Planner error: %s", e)
        return validate_plan(_fallback_plan(transcript))


def replan(transcript: str, context: str) -> dict:
    """Replan after a failed step (validated against tool registry)."""
    log.info("Replanning after failure: %r", transcript[:80])
    if "read_gmail" in context and (
        "JavaScript" in context or "Could not read Gmail" in context
    ):
        return validate_plan({
            "intent": "gmail_read_failed",
            "explanation": "Gmail opened but could not be read.",
            "steps": [],
            "clarification_needed": True,
            "clarification_question": (
                "Gmail opened in Chrome but I could not read the inbox. "
                "Enable Chrome → View → Developer → Allow JavaScript from Apple Events, "
                "or grant Accessibility permission for Terminal/Python (for clipboard fallback)."
            ),
        })
    return plan(_normalize_transcript(transcript), previous_context=context)


def resolve_step_args(args: dict, step_results: list[dict]) -> dict:
    """Public wrapper — resolves placeholders in step args using prior results."""
    return _resolve_placeholders(args, step_results)


def _fallback_plan(transcript: str) -> dict:
    return {
        "intent":                "unknown",
        "explanation":           "Could not understand the instruction.",
        "steps":                 [],
        "clarification_needed":  True,
        "clarification_question": (
            "I couldn't understand that. Please rephrase in English."
        ),
    }


def set_model(model_name: str):
    global MODEL
    MODEL = model_name
    log.info("LLM model: %s", model_name)
