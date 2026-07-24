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

from agent import platform as plat
from agent import llm as llm_cfg

log = logging.getLogger("intellivox.planner")

# Back-compat alias — always reads/writes shared Llama 3.2 config
MODEL = llm_cfg.get_model()

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

7. If you cannot satisfy the request safely or confidently, set clarification_needed=true
   (or return no steps). Prefer refusing unsafe requests (mass delete, hacking, money
   transfers, credential theft) over guessing.

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
  organize_files(directory, instruction="", dry_run=false)
                               ← organizes files per a spoken rule (by type, by extension, put PDFs into …)
                               PREFER this for "organise/organize/sort/tidy my files/Downloads/Desktop"

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
  compare_open_files(path_a, path_b, style="bullets")
                               ← OPENS File A and File B (visible), captures their content, sends both
                                 to the LLM, returns a comparison summary
                               USE THIS for "open two files and compare them" / "compare file A and file B"
  extract_pdf_to_spreadsheet(pdf_path, query="", fields="", output_path="")
                               ← locates info in a PDF and writes rows into an Excel/CSV spreadsheet
                               PREFER this for "put PDF info in spreadsheet/excel", "extract from PDF to sheet"
  write_spreadsheet(headers, rows, path="")
                               ← writes an already-extracted table to .xlsx/.csv
  update_presentation_from_document(source_path, presentation_path="", query="", output_path="", source_paths="")
                               ← updates (or creates) a PowerPoint using information from another document (or several)
                               PREFER this for "update presentation from PDF/document", "refresh slides using doc"
  create_presentation_from_document(source_path, query="", output_path="", source_paths="")
                               ← creates a new PowerPoint from a source document only


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

"open two files and compare them" / "compare File A and File B":
  Step 1: find_file(name="<file A name>")
  Step 2: find_file(name="<file B name>")
  Step 3: compare_open_files(path_a={{step_1_result.path}}, path_b={{step_2_result.path}}, style="bullets")
  ← opens both files so they are visible, captures content, LLM comparison summary

"locate info in the PDF and put it in a spreadsheet" / "extract from PDF to excel":
  Step 1: find_file(name="<pdf name>")
  Step 2: extract_pdf_to_spreadsheet(pdf_path={{step_1_result.path}}, query="<what to find>", fields="<optional cols>")
  ← reads PDF, locates the requested information, writes rows to ~/Documents/<name>_extracted.xlsx and opens it

"update the presentation using information from the PDF/document":
  Step 1: find_file(name="<source document name>")
  Step 2: find_file(name="<presentation name>")
  Step 3: update_presentation_from_document(source_path={{step_1_result.path}}, presentation_path={{step_2_result.path}}, query="<optional focus>")
  ← reads source doc + existing slides, rebuilds an updated .pptx (saved as *_updated.pptx) and opens it

"create a presentation from the PDF":
  Step 1: find_file(name="<pdf name>")
  Step 2: create_presentation_from_document(source_path={{step_1_result.path}})

"organise / organize / sort / tidy my Downloads/Desktop/files by type":
  Step 1: organize_files(directory="~/Downloads", instruction="<full spoken rule>")
  ← creates folders and moves files to match the spoken requirement
  For a named custom folder: Step 1 find_file(name="<folder>") then organize_files(directory={{step_1_result.path}}, instruction="...")

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


def _normalize_plan(result: dict) -> dict | None:
    """
    Normalize Llama 3.2 (esp. 1B) outputs into the expected plan schema.
    Small models often return {"tool": "...", "args": {...}} instead of steps[].
    """
    if not isinstance(result, dict):
        return None

    # Already valid-ish
    steps = result.get("steps")
    if isinstance(steps, list) and steps:
        cleaned = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            tool = s.get("tool")
            args = s.get("args") if isinstance(s.get("args"), dict) else {}
            if tool:
                cleaned.append({"tool": tool, "args": args})
        if cleaned:
            return {
                "intent": result.get("intent") or cleaned[0]["tool"],
                "explanation": result.get("explanation") or f"Running {cleaned[0]['tool']}.",
                "steps": cleaned,
                "clarification_needed": bool(result.get("clarification_needed", False)),
                "clarification_question": result.get("clarification_question"),
            }

    # Flat single-tool shape from small models
    tool = result.get("tool") or result.get("action") or result.get("name")
    if tool and isinstance(tool, str):
        args = result.get("args") if isinstance(result.get("args"), dict) else {}
        # Common mistake: {"intent":"open_app","tool":"notepad"} → tool is app name
        if result.get("intent") == "open_app" and tool not in ("open_app", "open_browser"):
            args = {**args, "name": args.get("name") or tool}
            tool = "open_app"
        elif result.get("intent") == "close_app" and tool != "close_app":
            args = {**args, "name": args.get("name") or tool}
            tool = "close_app"
        return {
            "intent": result.get("intent") or tool,
            "explanation": result.get("explanation") or f"Running {tool}.",
            "steps": [{"tool": tool, "args": args}],
            "clarification_needed": bool(result.get("clarification_needed", False)),
            "clarification_question": result.get("clarification_question"),
        }

    # Clarification-only response
    if result.get("clarification_needed") and result.get("clarification_question"):
        return {
            "intent": result.get("intent") or "unknown",
            "explanation": result.get("explanation") or "Need clarification.",
            "steps": [],
            "clarification_needed": True,
            "clarification_question": result["clarification_question"],
        }

    return None


def _heuristic_plan(transcript: str) -> dict | None:
    """
    Deterministic plans for common short voice commands.
    Used when the LLM returns empty/invalid JSON (e.g. Ollama GPU crash).
    """
    t = transcript.strip()
    low = t.lower().strip(" .,!?")

    # YouTube: search / play / comment — prefer computer_use after opening search
    if "youtube" in low and (
        re.search(r"\b(play|click|comment|search)\b", low)
        or "waka" in low
    ):
        # Extract search query after "search … for" / "search youtube for" / "youtube …"
        query = None
        m = re.search(
            r"(?:search(?:\s+youtube)?(?:\s+for)?|youtube(?:\s+search)?(?:\s+for)?)\s+(.+?)(?:\s+(?:and|then|click|play|use|comment)\b|$)",
            low,
        )
        if m:
            query = m.group(1).strip(" .,")
        if not query:
            # fallback: words between youtube and play/click
            m = re.search(r"youtube\b(.+?)(?:\bplay\b|\bclick\b|\bcomment\b|$)", low)
            if m:
                query = re.sub(r"\b(go\s+to|open|search|for|and)\b", " ", m.group(1))
                query = re.sub(r"\s+", " ", query).strip(" .,")
        if not query:
            query = "official music video"

        comment = None
        m = re.search(r"comment(?:\s+on\s+(?:this\s+)?video)?\s*:?\s*[\"“]?(.+?)[\"”]?$", low)
        if m:
            comment = m.group(1).strip(" .,")

        # Prefer an installed browser (Firefox first on Linux); honor spoken name.
        browser = plat.preferred_browser()
        for b in ("firefox", "brave", "chrome", "edge"):
            if b in low:
                browser = b
                break

        steps = [
            {"tool": "open_browser", "args": {"browser": browser}},
            {"tool": "youtube_search", "args": {"query": query, "browser": browser}},
        ]
        goal = (
            f"On the YouTube search results for '{query}', click the first matching "
            f"video thumbnail and make sure it plays."
        )
        if comment:
            goal += (
                f" Then scroll to comments, open the comment box, type exactly: {comment} "
                f"— and post the comment if logged in. If not logged in, stop and say so."
            )
        steps.append({"tool": "computer_use", "args": {"goal": goal}})

        return {
            "intent": "youtube_play",
            "explanation": f"Open browser, search YouTube for '{query}', play first result"
            + (f", then comment" if comment else ""),
            "steps": steps,
            "clarification_needed": False,
            "clarification_question": None,
        }

    # open <browser> and open/go to google [on it]
    m = re.match(
        r"^(?:please\s+)?(?:open|launch|start)\s+"
        r"(?:google\s+)?(chrome|firefox|brave|safari|edge|browser)"
        r"(?:\s+browser)?"
        r".*\b(?:open|go\s+to|navigate\s+to)\b.*\bgoogle\b",
        low,
    )
    if m:
        browser = m.group(1)
        if browser == "browser":
            browser = "chrome"
        return {
            "intent": "open_browser_google",
            "explanation": f"Opening {browser} and navigating to Google.",
            "steps": [
                {"tool": "open_browser", "args": {"browser": browser}},
                {"tool": "navigate_url", "args": {"url": "https://www.google.com", "browser": browser}},
            ],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # open google chrome / open chrome / open firefox
    m = re.match(
        r"^(?:please\s+)?(?:open|launch|start)\s+"
        r"(?:google\s+)?(chrome|firefox|brave|safari|edge)(?:\s+browser)?$",
        low,
    )
    if m:
        browser = m.group(1)
        return {
            "intent": "open_browser",
            "explanation": f"Opening {browser}.",
            "steps": [{"tool": "open_browser", "args": {"browser": browser}}],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # open <app>  (short app name only — don't swallow "and …")
    m = re.match(
        r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+)?(.+?)(?:\s+app(?:lication)?)?$",
        low,
    )
    if m:
        name = m.group(1).strip()
        if " and " in name or " then " in name:
            name = name.split(" and ")[0].split(" then ")[0].strip()
        name = re.sub(r"\b(the|a|an|app|application|please)\b", "", name).strip()
        if name and len(name.split()) <= 3:
            if name in ("chrome", "google chrome", "firefox", "brave", "safari", "edge", "browser"):
                browser = "chrome" if name in ("chrome", "google chrome", "browser") else name
                return {
                    "intent": "open_browser",
                    "explanation": f"Opening {browser}.",
                    "steps": [{"tool": "open_browser", "args": {"browser": browser}}],
                    "clarification_needed": False,
                    "clarification_question": None,
                }
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
        if " and " in name:
            name = name.split(" and ")[0].strip()
        if name and len(name.split()) <= 3:
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

    # compare PDF with dummy / compare two instances
    if re.search(r"\bcompar", low) and (
        "dummy" in low or "two instance" in low or "2 instance" in low
    ) and "pdf" in low and "open" not in low.split("compar")[0]:
        # try to extract a PDF/file name
        name = "Talent Hack"
        m = re.search(
            r"(?:compare|pdf)\s+(?:the\s+)?([a-z0-9][\w\s\-]+?)(?:\s+pdf)?(?:\s+with|\s+and|\s*$)",
            low,
        )
        if m:
            cand = m.group(1).strip()
            cand = re.sub(r"\b(with|dummy|document|file|instance|summary)\b", "", cand).strip()
            if cand and cand not in ("the", "a", "an"):
                name = cand.title()
        return {
            "intent": "compare_pdf_with_dummy",
            "explanation": f"Comparing {name} PDF summary with a dummy document.",
            "steps": [
                {"tool": "find_file", "args": {"name": name}},
                {"tool": "compare_pdf_with_dummy", "args": {"pdf_path": "{{step_1_result.path}}", "style": "bullets"}},
            ],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # open/compare two files (File A vs File B)
    if re.search(r"\bcompar", low) and (
        re.search(r"\b(two|2)\s+files?\b", low)
        or ("file a" in low and "file b" in low)
        or re.search(r"\bopen\b.*\b(two|both|and)\b.*\b(file|compare)\b", low)
        or re.search(r"\bcompare\b.+\band\b.+", low)
    ):
        # Try "compare X and Y" / "compare X with Y"
        name_a, name_b = "dummy_document", "sample_pdf_summary"
        m = re.search(
            r"compare\s+(?:the\s+)?(.+?)\s+(?:and|with|vs\.?|versus)\s+(?:the\s+)?(.+?)(?:\s+files?)?$",
            low,
        )
        if m:
            name_a = re.sub(r"\b(file|document|pdf|txt)\b", "", m.group(1)).strip(" .,") or name_a
            name_b = re.sub(r"\b(file|document|pdf|txt)\b", "", m.group(2)).strip(" .,") or name_b
        return {
            "intent": "compare_open_files",
            "explanation": f"Opening and comparing {name_a} with {name_b}.",
            "steps": [
                {"tool": "find_file", "args": {"name": name_a}},
                {"tool": "find_file", "args": {"name": name_b}},
                {
                    "tool": "compare_open_files",
                    "args": {
                        "path_a": "{{step_1_result.path}}",
                        "path_b": "{{step_2_result.path}}",
                        "style": "bullets",
                    },
                },
            ],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # update / create presentation from another document
    if re.search(r"\b(presentation|powerpoint|pptx|slides?|deck)\b", low) and (
        re.search(r"\b(update|refresh|revise|rewrite|sync)\b", low)
        or (
            re.search(r"\b(create|make|generate|build)\b", low)
            and re.search(r"\b(from|using|with)\b", low)
        )
        or (
            re.search(r"\b(using|from|with)\b", low)
            and re.search(r"\b(pdf|document|doc|file|info|information)\b", low)
        )
    ):
        source_name = "Talent Hack"
        ppt_name = ""

        # "update X presentation using Y" / "update presentation from Y"
        m = re.search(
            r"(?:update|refresh|revise|rewrite|sync)\s+(?:the\s+)?"
            r"([a-z0-9][\w\s\-]*?)\s*"
            r"(?:presentation|powerpoint|pptx|slides?|deck)"
            r".*?\b(?:using|from|with)\b\s+(?:the\s+|information\s+from\s+(?:the\s+)?)?"
            r"([a-z0-9][\w\s\-]+?)(?:\s+pdf|\s+document|\s+file)?(?:\s*$)",
            low,
        )
        if m:
            ppt_cand = re.sub(r"\b(the|a|an)\b", "", m.group(1)).strip(" .,")
            src_cand = re.sub(
                r"\b(the|a|an|pdf|document|file|info|information)\b",
                " ",
                m.group(2),
            )
            src_cand = re.sub(r"\s+", " ", src_cand).strip(" .,")
            if ppt_cand and ppt_cand not in ("the",):
                ppt_name = ppt_cand.title()
            if src_cand:
                source_name = src_cand.title()
        else:
            # "create presentation from X" / "update presentation from X"
            m2 = re.search(
                r"(?:presentation|powerpoint|pptx|slides?|deck).*\b(?:from|using|with)\b\s+"
                r"(?:the\s+|information\s+from\s+(?:the\s+)?)?"
                r"([a-z0-9][\w\s\-]+?)(?:\s+pdf|\s+document|\s+file)?(?:\s*$)",
                low,
            )
            if m2:
                src_cand = re.sub(
                    r"\b(the|a|an|pdf|document|file|info|information)\b",
                    " ",
                    m2.group(1),
                )
                src_cand = re.sub(r"\s+", " ", src_cand).strip(" .,")
                if src_cand:
                    source_name = src_cand.title()
            m3 = re.search(
                r"(?:from|in)\s+(?:the\s+)?([a-z0-9][\w\s\-]+?)\s+pdf",
                low,
            )
            if m3:
                cand = re.sub(r"\b(the|a|an)\b", "", m3.group(1)).strip(" .,")
                if cand:
                    source_name = cand.title()

            # optional presentation name: "update the Q3 deck"
            m4 = re.search(
                r"(?:update|refresh|revise)\s+(?:the\s+)?([a-z0-9][\w\s\-]+?)\s+"
                r"(?:presentation|powerpoint|pptx|slides?|deck)",
                low,
            )
            if m4:
                ppt_cand = re.sub(r"\b(the|a|an)\b", "", m4.group(1)).strip(" .,")
                if ppt_cand:
                    ppt_name = ppt_cand.title()

        query = ""
        mq = re.search(r"(?:focus(?:ing)?\s+on|about|emphasize)\s+(.+)$", low)
        if mq:
            query = mq.group(1).strip(" .,")

        create_only = bool(
            re.search(r"\b(create|make|generate|build)\b", low)
            and not re.search(r"\b(update|refresh|revise|rewrite|sync)\b", low)
        )

        steps = [{"tool": "find_file", "args": {"name": source_name}}]
        if create_only or not ppt_name:
            if create_only:
                args = {
                    "source_path": "{{step_1_result.path}}",
                    "open_after": True,
                }
                if query:
                    args["query"] = query
                steps.append({"tool": "create_presentation_from_document", "args": args})
                intent = "create_presentation_from_document"
                explanation = f"Creating a presentation from the {source_name} document."
            else:
                # Update without explicit deck name: find a .pptx + source
                steps.append({"tool": "find_file", "args": {"name": "pptx"}})
                args = {
                    "source_path": "{{step_1_result.path}}",
                    "presentation_path": "{{step_2_result.path}}",
                    "open_after": True,
                }
                if query:
                    args["query"] = query
                steps.append({"tool": "update_presentation_from_document", "args": args})
                intent = "update_presentation_from_document"
                explanation = (
                    f"Updating the presentation using information from {source_name}."
                )
        else:
            steps.append({"tool": "find_file", "args": {"name": ppt_name}})
            args = {
                "source_path": "{{step_1_result.path}}",
                "presentation_path": "{{step_2_result.path}}",
                "open_after": True,
            }
            if query:
                args["query"] = query
            steps.append({"tool": "update_presentation_from_document", "args": args})
            intent = "update_presentation_from_document"
            explanation = (
                f"Updating the {ppt_name} presentation using information from {source_name}."
            )

        return {
            "intent": intent,
            "explanation": explanation,
            "steps": steps,
            "clarification_needed": False,
            "clarification_question": None,
        }

    # organise / organize / sort / tidy files according to spoken requirement
    # also: "put all PDFs into Documents", "move images to Pictures"
    if (
        re.search(r"\b(organis(?:e|ing)|organiz(?:e|ing)|sort|tidy|arrange|clean\s+up)\b", low)
        and (
            re.search(r"\b(file|files|folder|directory|download|desktop|document|docs)\b", low)
            or re.search(r"\bby\s+(file\s+)?type\b|\bby\s+extension\b", low)
        )
    ) or (
        re.search(r"\b(put|move|send)\s+(?:all\s+)?(?:the\s+)?(?:pdfs?|images?|photos?|videos?|files?)\b", low)
        and re.search(r"\b(in(?:to)?|to)\b", low)
    ):
        directory = "~/Downloads"
        is_put_move = bool(
            re.search(
                r"\b(put|move|send)\s+(?:all\s+)?(?:the\s+)?(?:pdfs?|images?|photos?|videos?|files?)\b",
                low,
            )
            and re.search(r"\b(in(?:to)?|to)\b", low)
        )

        # Source folder (where files currently are). For "put X into Y", Y is destination
        # (carried in instruction), not the source — prefer explicit "from/in/on Downloads".
        source_match = re.search(
            r"\b(?:from|in|on)\s+(?:the\s+|my\s+)?(downloads?|desktop|documents?|docs|pictures?|photos?)\b",
            low,
        )
        if source_match:
            alias = source_match.group(1)
            if alias.startswith("download"):
                directory = "~/Downloads"
            elif alias == "desktop":
                directory = "~/Desktop"
            elif alias.startswith("doc"):
                directory = "~/Documents"
            elif alias.startswith("picture") or alias.startswith("photo"):
                directory = "~/Pictures"
        elif not is_put_move:
            for alias, folder in (
                ("download", "~/Downloads"),
                ("desktop", "~/Desktop"),
                ("document", "~/Documents"),
                ("docs", "~/Documents"),
                ("picture", "~/Pictures"),
                ("photo", "~/Pictures"),
            ):
                if alias in low:
                    directory = folder
                    break

        # Custom named folder: "organize the ProjectX folder"
        custom_folder = ""
        m = re.search(
            r"(?:organis\w+|organiz\w+|sort|tidy|arrange)\s+(?:the\s+|my\s+)?"
            r"([a-z0-9][\w\s\-]+?)\s+folder",
            low,
        )
        if m:
            cand = re.sub(r"\b(the|a|an|my)\b", "", m.group(1)).strip(" .,")
            if cand and cand not in ("download", "downloads", "desktop", "document", "documents"):
                custom_folder = cand.title()

        instruction = transcript.strip()
        # Prefer a concise rule if the user said "by type" / "by extension" / "put X into Y"
        if re.search(r"\bby\s+extension\b", low):
            instruction = "by extension"
        elif re.search(r"\bby\s+(file\s+)?type\b", low):
            instruction = "by type"
        else:
            m2 = re.search(
                r"((?:put|move|send)\s+.+)$|"
                r"((?:into|into folders?)\s+.+)$|"
                r"(sort\s+.+)$",
                low,
            )
            if m2:
                instruction = next(g for g in m2.groups() if g).strip()

        if custom_folder:
            return {
                "intent": "organize_files",
                "explanation": f"Organizing the {custom_folder} folder per your instructions.",
                "steps": [
                    {"tool": "find_file", "args": {"name": custom_folder}},
                    {
                        "tool": "organize_files",
                        "args": {
                            "directory": "{{step_1_result.path}}",
                            "instruction": instruction,
                        },
                    },
                ],
                "clarification_needed": False,
                "clarification_question": None,
            }

        return {
            "intent": "organize_files",
            "explanation": f"Organizing files in {directory} per your spoken requirement.",
            "steps": [
                {
                    "tool": "organize_files",
                    "args": {
                        "directory": directory,
                        "instruction": instruction,
                    },
                },
            ],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # locate PDF info → spreadsheet / excel / csv
    if (
        ("pdf" in low or "document" in low)
        and re.search(r"\b(spreadsheet|excel|csv|sheet|calc)\b", low)
        and re.search(r"\b(extract|locate|find|put|enter|fill|transfer|export|copy|into|to)\b", low)
    ) or (
        re.search(r"\b(extract|locate)\b", low)
        and "pdf" in low
        and re.search(r"\b(spreadsheet|excel|csv|sheet)\b", low)
    ):
        # Verbs / filler — never treat these as a PDF filename
        _stop = {
            "locate", "find", "extract", "get", "put", "enter", "fill", "transfer",
            "export", "copy", "information", "info", "data", "download", "downloads",
            "folder", "present", "which", "then", "into", "spreadsheet", "excel",
            "csv", "sheet", "document", "file", "the", "a", "an", "from", "in", "to",
            "and", "or", "my", "any", "some", "please", "that", "this", "on",
        }

        directory = None
        if re.search(r"\bdownloads?\b|\bdownload\s+folder\b", low):
            from pathlib import Path as _Path
            directory = str(_Path.home() / "Downloads")

        name = ""
        m = re.search(
            r"(?:from\s+(?:the\s+)?|in\s+(?:the\s+)?)([a-z0-9][\w\s\-]+?)\s+pdf",
            low,
        )
        if not m:
            m = re.search(r"([a-z0-9][\w\s\-]+?)\s+pdf", low)
        if m:
            cand = re.sub(
                r"\b(the|a|an|from|in|into|to|and|spreadsheet|excel|csv|sheet|"
                r"info|information|data|download|downloads|folder|present|which|"
                r"then|on|my|any|some)\b",
                " ",
                m.group(1),
            )
            cand = re.sub(r"\s+", " ", cand).strip(" .,")
            # Drop leading/trailing stopwords
            parts = [p for p in cand.split() if p.lower() not in _stop]
            cand = " ".join(parts).strip()
            if cand and len(cand) > 1 and cand.lower() not in _stop:
                name = cand.title()

        query = ""
        mq = re.search(
            r"(?:locate|find|extract|get|put|enter)\s+(?:the\s+)?(.+?)\s+"
            r"(?:from|in|into|to)\b",
            low,
        )
        if mq:
            query = re.sub(
                r"\b(pdf|document|spreadsheet|excel|csv|sheet|file)\b",
                "",
                mq.group(1),
            )
            query = re.sub(r"\s+", " ", query).strip(" .,")
            # "locate information" → empty query (extract broadly) rather than
            # treating "information" as a narrow search term
            if query.lower() in {"information", "info", "data", "content", "details"}:
                query = ""

        fields = ""
        mf = re.search(r"(?:columns?|fields?)\s*:?\s*([a-z0-9,\s\-]+)", low)
        if mf:
            fields = re.sub(r"\s+", " ", mf.group(1)).strip(" .,")

        args = {"pdf_path": "{{step_1_result.path}}", "open_after": True}
        if query:
            args["query"] = query
        if fields:
            args["fields"] = fields

        find_args: dict = {"ext": "pdf"}
        if name:
            find_args["name"] = name
        else:
            find_args["name"] = "pdf"
        if directory:
            find_args["directory"] = directory

        label = name or ("PDF in Downloads" if directory else "PDF")
        return {
            "intent": "extract_pdf_to_spreadsheet",
            "explanation": (
                f"Locating information in the {label} and entering it into a spreadsheet."
            ),
            "steps": [
                {"tool": "find_file", "args": find_args},
                {"tool": "extract_pdf_to_spreadsheet", "args": args},
            ],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # summarize PDF
    if re.search(r"\bsummar", low) and "pdf" in low:
        name = "Talent Hack"
        m = re.search(r"(?:summar\w+\s+(?:the\s+)?)([a-z0-9][\w\s\-]+?)\s+pdf", low)
        if m:
            name = m.group(1).strip().title()
        return {
            "intent": "summarize_pdf",
            "explanation": f"Summarizing the {name} PDF.",
            "steps": [
                {"tool": "find_file", "args": {"name": name}},
                {"tool": "read_pdf", "args": {"path": "{{step_1_result.path}}"}},
                {"tool": "summarize", "args": {"text": "{{step_2_result.text}}", "style": "bullets"}},
            ],
            "clarification_needed": False,
            "clarification_question": None,
        }

    # summarize codebase / folder
    if re.search(r"\bsummar", low) and re.search(r"\b(code|codebase|folder|project)\b", low):
        name = "IntelliVox"
        m = re.search(r"(?:folder|project|codebase)?\s*([a-z][\w\-]+)", low)
        # Prefer an explicit folder-like token
        for token in ("intellivox", "intelli vox"):
            if token in low:
                name = "IntelliVox"
                break
        return {
            "intent": "summarize_codebase",
            "explanation": f"Summarizing the {name} codebase.",
            "steps": [
                {"tool": "find_file", "args": {"name": name}},
                {"tool": "summarize_codebase", "args": {"directory": "{{step_1_result.path}}", "style": "bullets"}},
            ],
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

    # Refuse clearly unsafe requests before heuristics / LLM
    from agent import safety as safety_mod
    pre = safety_mod.assess_request(transcript)
    if not pre.can_proceed and pre.kind in ("unsafe", "impossible"):
        return {
            "intent": pre.kind,
            "explanation": pre.message,
            "steps": [],
            "clarification_needed": True,
            "clarification_question": pre.message,
        }

    # Llama 3.2:1b is weak on long JSON tool plans — prefer heuristics first
    heuristic = _heuristic_plan(transcript)
    if heuristic and not previous_context:
        log.info("Using heuristic plan: %s", heuristic.get("intent"))
        return heuristic

    system_prompt = _build_system_prompt()

    user_msg = f'User said: "{transcript}"'
    if previous_context:
        user_msg += f'\n\nContext from previous steps:\n{previous_context}'
    user_msg += "\n\nRespond with ONLY a valid JSON object matching the schema. No prose."

    try:
        raw = llm_cfg.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            model=llm_cfg.get_model(),
            temperature=0.05,
            format="json",
            num_predict=512,
        )
        log.debug("LLM raw: %s", raw[:500] if raw else "<empty>")

        if not raw:
            raise ValueError(
                "Ollama returned empty content (often a GPU/Vulkan crash). "
                "Fix: restart ollama with AMD-only Vulkan — see voice/fix-ollama-gpu.sh"
            )

        result = _extract_json(raw)
        if not result:
            raise json.JSONDecodeError("No JSON object in model output", raw, 0)

        normalized = _normalize_plan(result)
        if not normalized:
            raise ValueError(f"Plan schema incomplete for Llama 3.2: {result!r}")

        log.info("Plan: [%s] %d steps — %s",
                 normalized.get("intent"), len(normalized.get("steps", [])),
                 normalized.get("explanation"))
        return normalized

    except Exception as e:
        log.error("Planner LLM failed: %s", e)
        if heuristic:
            log.info("Using heuristic plan: %s", heuristic.get("intent"))
            return heuristic
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
    llm_cfg.set_model(model_name)
    MODEL = llm_cfg.get_model()
    log.info("LLM model: %s", MODEL)
