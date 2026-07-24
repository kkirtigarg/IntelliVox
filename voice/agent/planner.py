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
  compare_summarize(text_a, text_b, label_a, label_b, style="bullets")
                               ← compare two sources (e.g. mail + PDF)
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

11b. COMPARE two sources — use compare_summarize (NOT single summarize):
   "Compare my mail with report.pdf and summarize" → open_gmail, read_gmail, find_file, read_pdf, compare_summarize
   "Compare report.pdf and invoice.pdf and summarize" → find_file, read_pdf, find_file, read_pdf, compare_summarize

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
        query = re.sub(r"\s+and\s+post\s+a\s+.+?\s+comment.*$", "", query, flags=re.I)
        query = re.sub(r"\s+and\s+post\s+.+?\s+comment.*$", "", query, flags=re.I)
        query = re.sub(r"\s+and\s+post\s+(?:a\s+)?comment.*$", "", query, flags=re.I)
        query = re.sub(r"\s+and\s+comment.*$", "", query, flags=re.I)
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


def _wants_compare(transcript: str) -> bool:
    lower = transcript.lower()
    if any(
        k in lower
        for k in ("compare", "comparison", "difference", "differences", " versus ", " vs ")
    ):
        return True
    # Whisper often hears "compare" as "compile/compiled"
    if re.search(r"\bcompil(?:e|ed|ing)\b", lower):
        return True
    return False


def _wants_compare_summary(transcript: str) -> bool:
    lower = transcript.lower()
    if any(k in lower for k in ("summar", "summary", "summarise", "overview")):
        return True
    if _wants_compare(transcript) and any(k in lower for k in ("pdf", "pdfs", "document")):
        return True
    return False


def _compare_search_directory(transcript: str) -> str | None:
    lower = transcript.lower()
    if "download" in lower:
        return os.path.join(HOME, "Downloads")
    if "desktop" in lower:
        return os.path.join(HOME, "Desktop")
    if "documents" in lower:
        return os.path.join(HOME, "Documents")
    return None


def _clean_document_name(name: str) -> str:
    name = re.sub(r"\s+which\b.*$", "", name, flags=re.I)
    name = re.sub(r"\s+in\s+(?:the\s+)?(?:download|downloads|desktop|documents).*$", "", name, flags=re.I)
    name = re.sub(r"\s+(?:and\s+)?summarize\b.*$", "", name, flags=re.I)
    name = re.sub(r"\s+(?:and\s+)?summarise\b.*$", "", name, flags=re.I)
    name = re.sub(r"\s+it\.?$", "", name, flags=re.I)
    name = re.sub(r"\s+(?:two\s+)?(?:pdf|pdfs|document)s?\s*$", "", name, flags=re.I)
    name = re.sub(r"\s+so\b.*$", "", name, flags=re.I)
    name = re.sub(r"\s+that\b.*$", "", name, flags=re.I)
    name = re.sub(r"'s$", "", name, flags=re.I)
    if re.fullmatch(r"[a-z]+s", name.strip(), re.I) and len(name.strip()) > 4:
        name = name.strip()[:-1]
    return name.strip(" .,\"'")


def _is_garbage_compare_name(name: str) -> bool:
    n = name.lower().strip()
    if len(n) < 2:
        return True
    if n in ("comparison", "compare", "as well", "well", "summarize", "download", "downloads"):
        return True
    if "as well" in n or n.startswith("comparison"):
        return True
    return False


def _extract_compare_document_names(transcript: str) -> list[str]:
    """Extract two document titles when user says 'compare X and Y' (PDF context)."""
    lower = transcript.lower()
    if not any(k in lower for k in ("pdf", "pdfs", "document", "resume")):
        return []

    compare_prefix = r"(?:compare|comparison|compil(?:e|ed|ing)|compared|comparing)"
    patterns = [
        rf"{compare_prefix}\s+(?:as\s+well\s+)?(.+?)\s+pdf\s+and\s+(.+?)\s+(?:resume\s+)?pdf",
        rf"{compare_prefix}\s+(.+?)\s+and\s+(.+)",
        rf"{compare_prefix}\s+(.+?)\s+with\s+(.+)",
        rf"{compare_prefix}\s+(.+?)\s+versus\s+(.+)",
        r"(.+?)\s+and\s+(.+?)\s+(?:pdf|pdfs|resume)\b",
        r"\band\s+(.+?)\s+resume\s+pdf",
    ]
    for pat in patterns:
        m = re.search(pat, transcript, re.I)
        if not m:
            continue
        if m.lastindex == 1:
            b = _clean_document_name(m.group(1))
            return ["", b] if len(b) >= 2 and not _is_garbage_compare_name(b) else []
        a = _clean_document_name(m.group(1))
        b = _clean_document_name(m.group(2))
        if _is_garbage_compare_name(a):
            a = ""
        if _is_garbage_compare_name(b):
            continue
        if len(b) >= 2 and (len(a) >= 2 or a == ""):
            return [a, b]
    return []


def _resolve_compare_pdf_names(transcript: str) -> list[str]:
    """PDF filenames or spoken document titles for compare."""
    pdfs = _extract_pdf_names(transcript)
    if len(pdfs) >= 2:
        return pdfs[:2]
    spoken = _extract_compare_document_names(transcript)
    if len(spoken) >= 2:
        return spoken[:2]
    if any(k in transcript.lower() for k in ("pdf", "pdfs", "document", "resume")):
        if len(spoken) == 1:
            return spoken
    return pdfs


def _extract_pdf_names(transcript: str) -> list[str]:
    """Find distinct PDF filenames mentioned in the transcript."""
    names: list[str] = []
    for m in re.finditer(r"([\w.-]+)\.(pdf|pd)\b", transcript, re.I):
        base = m.group(1).strip()
        name = f"{base}.pdf"
        if name not in names and len(name) >= 5:
            names.append(name)
    return names


def _extract_compare_second_source(transcript: str) -> tuple[str, str] | None:
    """Return (kind, name) for the non-mail source. kind is 'pdf' or 'file'."""
    pdfs = _extract_pdf_names(transcript)
    if len(pdfs) == 1:
        return "pdf", pdfs[0]

    lower = transcript.lower()
    patterns = [
        r"(?:mail|email|gmail|inbox).*?(?:and|with|to)\s+(?:the\s+)?([\w.-]+?)(?:\s+pdf\b|\s+on\b|$)",
        r"(?:compare|with|and|to)\s+(?:the\s+)?([\w.-]+?)\s+pdf\b",
        r"pdf\s+(?:named|called|file)?\s*['\"]?([\w.-]+)['\"]?",
    ]
    for pat in patterns:
        m = re.search(pat, lower, re.I)
        if m:
            name = m.group(1).strip(" .")
            if len(name) >= 2:
                return "pdf", name if name.lower().endswith(".pdf") else f"{name}.pdf"
    if "pdf" in lower or "document" in lower:
        return "pdf", ""
    return None


def _pdf_search_name(filename: str) -> str:
    base = filename[:-4] if filename.lower().endswith(".pdf") else filename
    base = re.sub(r"'s$", "", base.strip(), flags=re.I)
    if re.fullmatch(r"[a-z]+s", base, re.I) and len(base) > 4:
        base = base[:-1]
    return base.strip(" .,\"'")


def _try_compare_pdfs_plan(transcript: str) -> dict | None:
    """Compare two PDFs and summarize differences."""
    lower = transcript.lower()
    if not _wants_compare(transcript):
        return None
    if not _wants_compare_summary(transcript):
        return None

    pdfs = _resolve_compare_pdf_names(transcript)
    if len(pdfs) < 2:
        return None

    has_mail = any(k in lower for k in ("mail", "email", "gmail", "inbox"))
    if has_mail and len(_extract_pdf_names(transcript)) == 1:
        return None

    pdf_a, pdf_b = pdfs[0], pdfs[1]
    if _is_garbage_compare_name(pdf_a):
        pdf_a = ""
    if _is_garbage_compare_name(pdf_b):
        return None
    search_dir = _compare_search_directory(transcript)
    pair_args = {
        "name_a": _pdf_search_name(pdf_a) if pdf_a else "",
        "name_b": _pdf_search_name(pdf_b),
        "hint_text": transcript,
    }
    if search_dir:
        pair_args["directory"] = search_dir

    steps: list[dict] = [
        {"tool": "find_compare_pdf_pair", "args": pair_args},
        {"tool": "read_pdf", "args": {"path": "{{step_1_result.path_a}}"}},
        {"tool": "read_pdf", "args": {"path": "{{step_1_result.path_b}}"}},
        {
            "tool": "compare_summarize",
            "args": {
                "text_a": "{{step_2_result.text}}",
                "text_b": "{{step_3_result.text}}",
                "label_a": "{{step_1_result.label_a}}",
                "label_b": "{{step_1_result.label_b}}",
                "style": "bullets",
            },
        },
    ]

    where = " in Downloads" if search_dir and "Download" in search_dir else ""
    return validate_plan({
        "intent": "compare_pdfs",
        "explanation": f"Comparing '{pdf_a}' with '{pdf_b}'{where} and summarizing.",
        "steps": steps,
        "clarification_needed": False,
        "clarification_question": None,
    })


def _try_compare_summary_plan(transcript: str) -> dict | None:
    """Compare mail (Gmail) with a PDF/document, then summarize differences."""
    lower = transcript.lower()
    if not _wants_compare(transcript):
        return None
    if not _wants_compare_summary(transcript):
        return None

    if len(_resolve_compare_pdf_names(transcript)) >= 2:
        return None

    has_mail = any(k in lower for k in ("mail", "email", "gmail", "inbox"))
    second = _extract_compare_second_source(transcript)
    if not has_mail or not second:
        return None

    _kind, doc_name = second
    if not doc_name:
        return validate_plan({
            "intent": "compare_summary",
            "explanation": "Need a document name to compare with mail.",
            "steps": [],
            "clarification_needed": True,
            "clarification_question": (
                "Which PDF should I compare with your mail? "
                "Try: 'Compare my mail with report.pdf and summarize.'"
            ),
        })

    read_count = _mail_read_count(transcript, True, True)
    search_name = _pdf_search_name(doc_name)

    steps: list[dict] = [
        {"tool": "open_gmail", "args": {"browser": "chrome", "recent": True}},
        {"tool": "read_gmail", "args": {"limit": read_count, "wait": 8, "browser": "chrome"}},
        {"tool": "find_file", "args": {"name": search_name}},
        {"tool": "read_pdf", "args": {"path": "{{step_3_result.path}}"}},
        {
            "tool": "compare_summarize",
            "args": {
                "text_a": "{{step_2_result.body}}",
                "text_b": "{{step_4_result.text}}",
                "label_a": "Gmail inbox",
                "label_b": doc_name,
                "style": "bullets",
            },
        },
    ]

    return validate_plan({
        "intent": "compare_summary",
        "explanation": f"Comparing Gmail with '{doc_name}' and summarizing.",
        "steps": steps,
        "clarification_needed": False,
        "clarification_question": None,
    })


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
    if _wants_compare(transcript):
        return None
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

_PLACEHOLDER = re.compile(r"\{\{step_(\d+)_result\.(\w+)\}\}")


def _resolve_placeholders(args: dict, step_results: list[dict]) -> dict:
    """
    Replace {{step_N_result.field}} placeholders with actual values
    from previous step results. Enables chaining.
    """
    out: dict = {}
    changed = False

    for key, val in args.items():
        if not isinstance(val, str) or not _PLACEHOLDER.search(val):
            out[key] = val
            continue

        def _replace(match: re.Match) -> str:
            step_idx = int(match.group(1)) - 1
            field = match.group(2)
            if 0 <= step_idx < len(step_results):
                value = step_results[step_idx].get(field, "")
                if value not in (None, ""):
                    return str(value)
            return match.group(0)

        resolved = _PLACEHOLDER.sub(_replace, val)
        if resolved != val:
            changed = True
        out[key] = resolved

    return out if changed else args


def _normalize_transcript(transcript: str) -> str:
    """Fix common speech-to-text typos before routing."""
    text = transcript
    text = re.sub(r"\.pd\b", ".pdf", text, flags=re.I)
    text = re.sub(r"\bcompare_and_summarize\b", "compare summarize", text, flags=re.I)
    text = re.sub(r"\bcompil(?:e|ed|ing)\b", "compare", text, flags=re.I)
    text = re.sub(r"\bjaishwal\b", "jaiswal", text, flags=re.I)
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

    compare_pdfs_plan = _try_compare_pdfs_plan(transcript)
    if compare_pdfs_plan:
        log.info("Plan: [%s] %d steps — %s (deterministic)",
                 compare_pdfs_plan.get("intent"), len(compare_pdfs_plan.get("steps", [])),
                 compare_pdfs_plan.get("explanation"))
        return compare_pdfs_plan

    compare_plan = _try_compare_summary_plan(transcript)
    if compare_plan:
        log.info("Plan: [%s] %d steps — %s (deterministic)",
                 compare_plan.get("intent"), len(compare_plan.get("steps", [])),
                 compare_plan.get("explanation"))
        return compare_plan

    mail_plan = _try_mail_plan(transcript)
    if mail_plan:
        log.info("Plan: [%s] %d steps — %s (deterministic)",
                 mail_plan.get("intent"), len(mail_plan.get("steps", [])),
                 mail_plan.get("explanation"))
        return mail_plan

    if _wants_compare(transcript) and _wants_compare_summary(transcript):
        return validate_plan({
            "intent": "compare_clarify",
            "explanation": "Need clearer PDF names for compare.",
            "steps": [],
            "clarification_needed": True,
            "clarification_question": (
                "Name both PDFs clearly. Example: "
                "'Compare Shivam Jaiswar Raju.pdf and Jaya Rai Raju.pdf in Downloads and summarize.'"
            ),
        })

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
    norm = _normalize_transcript(transcript)
    if _wants_compare(norm) and (
        "find_file" in context or "find_compare_pdf_pair" in context
    ) and ("No file found" in context or "No PDF" in context):
        return validate_plan({
            "intent": "compare_find_failed",
            "explanation": "Could not find one or both PDFs.",
            "steps": [],
            "clarification_needed": True,
            "clarification_question": (
                "I could not find those PDFs in Downloads. Check the names, or say something like "
                "'Compare Shivam and Jaya PDFs in Downloads and summarize.'"
            ),
        })
    return plan(norm, previous_context=context)


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
