"""
agent/tools/document.py
Document understanding tools: read PDFs, summarize content,
compare two document instances, answer questions,
and extract PDF fields into a spreadsheet.
"""
import csv
import json
import logging
import os
import re
from pathlib import Path

from agent import llm as llm_cfg

log = logging.getLogger("intellivox.document")

HOME = str(Path.home())
# Cap input for Llama 3.2:1b (override with INTELLIVOX_MAX_CHARS)
MAX_CHARS = llm_cfg.MAX_CHARS
SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "samples"
DEFAULT_DUMMY_PATH = str(SAMPLES_DIR / "dummy_document.txt")
DOCUMENTS_DIR = Path.home() / "Documents"

# Baseline dummy content used when comparing against a PDF summary
DUMMY_DOCUMENT_CONTENT = """\
IntelliVox Sample Document (Dummy Instance)
==========================================

Title: Product Overview — Voice Desktop Assistant

Purpose:
This is a placeholder baseline document used for pairwise comparison
against a PDF summary. It describes a generic voice-controlled desktop
assistant that can open apps, search the web, and summarize files.

Key points:
• Voice input is transcribed locally with Whisper
• An LLM planner turns speech into tool calls
• Supported actions include browser, desktop, and file tools
• Document tools can read PDFs and produce short summaries
• Safety checks gate destructive actions before they run

Scope (intentionally limited):
This dummy instance does not include pricing, hiring timelines,
hackathon rules, or any event-specific details. Those details are
expected to appear only in the real PDF summary being compared.

Last updated: 2026-07-24
"""


# ── PDF text extraction ────────────────────────────────────────────────────────

def read_pdf(path: str) -> dict:
    """
    Extract all text from a PDF file.
    Returns: { success, text, page_count, path }
    """
    expanded = str(Path(path).expanduser().resolve())
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}

    # Try pdfplumber first (better layout handling)
    try:
        import pdfplumber
        pages_text = []
        with pdfplumber.open(expanded) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t.strip())
        text = "\n\n".join(pages_text)
        if text.strip():
            log.info("pdfplumber extracted %d chars from %s", len(text), expanded)
            return {
                "success":    True,
                "text":       text[:MAX_CHARS],
                "page_count": len(pages_text),
                "path":       expanded,
                "truncated":  len(text) > MAX_CHARS,
            }
    except Exception as e:
        log.warning("pdfplumber failed: %s", e)

    # Fallback: pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(expanded)
        pages_text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t.strip())
        text = "\n\n".join(pages_text)
        log.info("pypdf extracted %d chars", len(text))
        return {
            "success":    True,
            "text":       text[:MAX_CHARS],
            "page_count": len(reader.pages),
            "path":       expanded,
            "truncated":  len(text) > MAX_CHARS,
        }
    except Exception as e:
        return {"success": False, "message": f"Could not read PDF: {e}"}


# ── LLM-powered summarization ─────────────────────────────────────────────────

def summarize(text: str, style: str = "concise") -> dict:
    """
    Summarize a block of text using the local Ollama LLM.
    style: "concise" | "detailed" | "bullets"
    Returns: { success, summary }
    """
    if not text or not text.strip():
        return {"success": False, "message": "No text to summarize"}

    style_prompts = {
        "concise":  "Summarize the following in 3-5 clear sentences. Be direct and factual.",
        "detailed": "Write a detailed summary with key points, covering all main topics.",
        "bullets":  "Summarize as a bullet-point list. Use • for each point. Max 10 bullets.",
    }
    instruction = style_prompts.get(style, style_prompts["concise"])

    prompt = f"{instruction}\n\nContent:\n{text[:MAX_CHARS]}"

    try:
        summary = llm_cfg.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            num_predict=400,
        )
        return {"success": True, "summary": summary}
    except Exception as e:
        return {"success": False, "message": f"Summarization failed: {e}"}


def summarize_codebase(directory: str = "", style: str = "bullets", path: str = "") -> dict:
    """
    Read source files in a project folder and summarize the codebase.
    Skips node_modules, .git, venv, dist, etc.
    Returns: { success, summary, files_read, directory }
    """
    directory = directory or path
    if not directory:
        return {"success": False, "message": "No directory provided"}
    expanded = str(Path(directory).expanduser().resolve())
    if not os.path.isdir(expanded):
        return {"success": False, "message": f"Not a directory: {directory}"}

    if not expanded.startswith(HOME):
        return {"success": False, "message": "Access denied: outside home directory"}

    code_ext = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".json", ".html", ".css",
        ".cjs", ".mjs", ".yaml", ".yml", ".toml", ".rs", ".go", ".java", ".sql",
    }
    skip_dirs = {
        ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "release",
        "build", ".cursor", "audit_logs",
    }

    collected: list[tuple[str, str]] = []
    total_chars = 0

    for root, dirs, files in os.walk(expanded):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            ext = Path(fname).suffix.lower()
            if ext not in code_ext:
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, expanded)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            chunk = f"--- {rel} ---\n{content}\n"
            if total_chars + len(chunk) > MAX_CHARS:
                break
            collected.append((rel, content))
            total_chars += len(chunk)
            if len(collected) >= 40:
                break
        if total_chars >= MAX_CHARS or len(collected) >= 40:
            break

    if not collected:
        return {"success": False, "message": f"No readable source files in {expanded}"}

    bundle = "\n".join(f"--- {rel} ---\n{body}" for rel, body in collected)
    style_prompts = {
        "concise": "Summarize this codebase in 3-5 sentences: purpose, main components, and tech stack.",
        "detailed": "Write a detailed summary: architecture, folders, key files, and how parts connect.",
        "bullets": "Summarize as bullet points: project purpose, folder structure, main modules, tech stack.",
    }
    instruction = style_prompts.get(style, style_prompts["bullets"])
    prompt = f"{instruction}\n\nCodebase ({len(collected)} files from {expanded}):\n{bundle[:MAX_CHARS]}"

    try:
        summary = llm_cfg.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            num_predict=500,
        )
        return {
            "success": True,
            "summary": summary,
            "files_read": len(collected),
            "directory": expanded,
        }
    except Exception as e:
        return {"success": False, "message": f"Codebase summarization failed: {e}"}


def answer_question(text: str, question: str) -> dict:
    """
    Answer a specific question about a document's content using local LLM.
    Returns: { success, answer }
    """
    if not text or not question:
        return {"success": False, "message": "Need both text and question"}

    prompt = (
        f"Answer this question based ONLY on the provided document content. "
        f"Be specific and cite the relevant section.\n\n"
        f"Question: {question}\n\n"
        f"Document content:\n{text[:MAX_CHARS]}"
    )

    try:
        answer = llm_cfg.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            num_predict=400,
        )
        return {"success": True, "answer": answer}
    except Exception as e:
        return {"success": False, "message": f"Q&A failed: {e}"}


# ── Dummy instance + pairwise comparison ──────────────────────────────────────

def create_dummy_file(path: str = "", content: str = "") -> dict:
    """
    Create (or overwrite) a dummy document used as one side of a comparison.
    Defaults to voice/samples/dummy_document.txt with built-in baseline text.
    Returns: { success, path, content, message }
    """
    target = path.strip() if path else DEFAULT_DUMMY_PATH
    expanded = str(Path(target).expanduser().resolve())
    body = content.strip() if content else DUMMY_DOCUMENT_CONTENT

    try:
        Path(expanded).parent.mkdir(parents=True, exist_ok=True)
        with open(expanded, "w", encoding="utf-8") as f:
            f.write(body)
        log.info("Created dummy document at %s (%d chars)", expanded, len(body))
        return {
            "success": True,
            "path": expanded,
            "content": body,
            "message": f"Dummy file created at {expanded}",
        }
    except Exception as e:
        return {"success": False, "message": f"Could not create dummy file: {e}"}


def compare_documents(
    text_a: str = "",
    text_b: str = "",
    label_a: str = "Instance A",
    label_b: str = "Instance B",
    style: str = "bullets",
) -> dict:
    """
    Compare exactly two document instances and produce a comparison summary.
    Typical use: text_a = PDF summary, text_b = dummy file content.
    Returns: { success, summary, label_a, label_b }
    """
    a = (text_a or "").strip()
    b = (text_b or "").strip()
    if not a or not b:
        return {
            "success": False,
            "message": "Need two non-empty text instances to compare",
        }

    # Keep both sides under the token budget (split evenly)
    half = MAX_CHARS // 2
    a, b = a[:half], b[:half]

    style_prompts = {
        "concise": (
            "Write a concise comparison summary in 4-6 sentences. "
            "Cover similarities, differences, and what is unique to each side."
        ),
        "detailed": (
            "Write a detailed comparison summary covering: shared themes, "
            "key differences, missing topics on each side, and an overall verdict."
        ),
        "bullets": (
            "Compare the two instances as bullet points with sections:\n"
            "• Similarities\n• Differences\n• Only in first instance\n"
            "• Only in second instance\n• Overall summary (2-3 sentences)"
        ),
    }
    instruction = style_prompts.get(style, style_prompts["bullets"])

    prompt = (
        f"{instruction}\n\n"
        f"You are comparing exactly TWO instances at a time.\n"
        f"Create the summary WHILE comparing them — do not summarize each alone.\n\n"
        f"=== {label_a} ===\n{a}\n\n"
        f"=== {label_b} ===\n{b}"
    )

    try:
        summary = llm_cfg.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            num_predict=500,
        )
        return {
            "success": True,
            "summary": summary,
            "label_a": label_a,
            "label_b": label_b,
        }
    except Exception as e:
        return {"success": False, "message": f"Comparison failed: {e}"}


def compare_pdf_with_dummy(
    pdf_path: str,
    dummy_path: str = "",
    style: str = "bullets",
    save_pdf_summary: bool = True,
) -> dict:
    """
    End-to-end pairwise compare:
      1) Read PDF → summarize it (instance A, optionally saved to disk)
      2) Ensure a dummy file exists (instance B)
      3) Compare the two instances and return a comparison summary

    Returns: {
      success, summary, pdf_summary, pdf_summary_path,
      dummy_path, pdf_path, label_a, label_b
    }
    """
    pdf = read_pdf(pdf_path)
    if not pdf.get("success"):
        return pdf

    pdf_sum = summarize(pdf["text"], style="detailed")
    if not pdf_sum.get("success"):
        return pdf_sum

    pdf_summary_text = pdf_sum["summary"]
    pdf_summary_path = ""

    if save_pdf_summary:
        stem = Path(pdf["path"]).stem
        out_dir = Path(dummy_path).expanduser().resolve().parent if dummy_path else SAMPLES_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_summary_path = str(out_dir / f"{stem}_summary.txt")
        try:
            with open(pdf_summary_path, "w", encoding="utf-8") as f:
                f.write(pdf_summary_text)
        except Exception as e:
            log.warning("Could not save PDF summary file: %s", e)
            pdf_summary_path = ""

    dummy = create_dummy_file(path=dummy_path or DEFAULT_DUMMY_PATH)
    if not dummy.get("success"):
        return dummy

    comparison = compare_documents(
        text_a=pdf_summary_text,
        text_b=dummy["content"],
        label_a="PDF Summary",
        label_b="Dummy Document",
        style=style,
    )
    if not comparison.get("success"):
        return comparison

    return {
        "success": True,
        "summary": comparison["summary"],
        "pdf_summary": pdf_summary_text,
        "pdf_summary_path": pdf_summary_path,
        "dummy_path": dummy["path"],
        "pdf_path": pdf["path"],
        "label_a": "PDF Summary",
        "label_b": "Dummy Document",
        "message": "Compared PDF summary with dummy document",
    }


# ── Open two files → capture content → LLM comparison ─────────────────────────

def _capture_open_file_content(path: str, side: str = "A") -> dict:
    """
    Open a file with the default app (so it is visible), then capture its text.
    PDFs use read_pdf; other files use plain-text read.
    Returns: { success, path, label, content, opened, screenshot, message }
    """
    import time
    from agent import platform as plat

    expanded = str(Path(path).expanduser().resolve())
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}

    label = Path(expanded).name
    opened = False
    open_msg = ""

    open_res = plat.open_path(expanded)
    if open_res.get("success"):
        opened = True
        open_msg = open_res.get("message", f"Opened {label}")
        time.sleep(1.2)  # let the viewer window appear
    else:
        open_msg = open_res.get("message", f"Could not open {label}")
        log.warning("open_path soft-fail for %s: %s — still capturing from disk", expanded, open_msg)

    # Screenshot while the file is (hopefully) visible — never block the compare
    shot_path = f"/tmp/intellivox_compare_{side}_{Path(expanded).stem}.png"
    screenshot = None
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(plat.take_screenshot, shot_path)
            if fut.result(timeout=4.0):
                screenshot = shot_path
    except Exception as e:
        log.debug("Screenshot skipped: %s", e)

    ext = Path(expanded).suffix.lower()
    if ext == ".pdf":
        captured = read_pdf(expanded)
        if not captured.get("success"):
            return {
                "success": False,
                "message": captured.get("message", "Failed to read PDF"),
                "path": expanded,
                "opened": opened,
            }
        content = captured.get("text", "")
    else:
        try:
            with open(expanded, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to read file: {e}",
                "path": expanded,
                "opened": opened,
            }

    if not (content or "").strip():
        return {
            "success": False,
            "message": f"No readable text content in {label}",
            "path": expanded,
            "opened": opened,
            "screenshot": screenshot,
        }

    return {
        "success": True,
        "path": expanded,
        "label": label,
        "content": content[:MAX_CHARS],
        "opened": opened,
        "screenshot": screenshot,
        "message": open_msg,
    }


def compare_open_files(
    path_a: str = "",
    path_b: str = "",
    style: str = "bullets",
    file_a: str = "",
    file_b: str = "",
) -> dict:
    """
    Open File A and File B, capture their content while visible, send both to the
    LLM, and return a comparison summary.

    Accepts path_a/path_b (preferred) or file_a/file_b aliases.
    Returns: {
      success, summary, path_a, path_b, label_a, label_b,
      opened_a, opened_b, screenshot_a, screenshot_b, message
    }
    """
    path_a = (path_a or file_a or "").strip()
    path_b = (path_b or file_b or "").strip()
    if not path_a or not path_b:
        return {
            "success": False,
            "message": "Need two file paths (path_a and path_b) to compare",
        }

    cap_a = _capture_open_file_content(path_a, side="A")
    if not cap_a.get("success"):
        return {
            "success": False,
            "message": f"File A: {cap_a.get('message', 'capture failed')}",
            "path_a": path_a,
        }

    cap_b = _capture_open_file_content(path_b, side="B")
    if not cap_b.get("success"):
        return {
            "success": False,
            "message": f"File B: {cap_b.get('message', 'capture failed')}",
            "path_a": cap_a["path"],
            "path_b": path_b,
        }

    comparison = compare_documents(
        text_a=cap_a["content"],
        text_b=cap_b["content"],
        label_a=f"File A ({cap_a['label']})",
        label_b=f"File B ({cap_b['label']})",
        style=style,
    )
    if not comparison.get("success"):
        return comparison

    return {
        "success": True,
        "summary": comparison["summary"],
        "path_a": cap_a["path"],
        "path_b": cap_b["path"],
        "label_a": cap_a["label"],
        "label_b": cap_b["label"],
        "opened_a": cap_a.get("opened", False),
        "opened_b": cap_b.get("opened", False),
        "screenshot_a": cap_a.get("screenshot"),
        "screenshot_b": cap_b.get("screenshot"),
        "message": (
            f"Opened and compared {cap_a['label']} vs {cap_b['label']}"
        ),
    }


# ── PDF → spreadsheet ─────────────────────────────────────────────────────────

def _extract_json_object(raw: str) -> dict | list | None:
    """Best-effort parse of a JSON object/array from LLM output."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first { ... } or [ ... ]
    for start_ch, end_ch in (("{", "}"), ("[", "]")):
        start = text.find(start_ch)
        end = text.rfind(end_ch)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _normalize_table(data) -> tuple[list[str], list[list[str]]]:
    """
    Normalize LLM JSON into (headers, rows).
    Accepts:
      { "headers": [...], "rows": [[...], ...] }
      { "columns": [...], "data": [...] }
      [ {"col": "val", ...}, ... ]
    """
    if data is None:
        return [], []

    if isinstance(data, list):
        if not data:
            return [], []
        if all(isinstance(r, dict) for r in data):
            headers: list[str] = []
            for row in data:
                for k in row.keys():
                    if str(k) not in headers:
                        headers.append(str(k))
            rows = [[str(row.get(h, "")).strip() for h in headers] for row in data]
            return headers, rows
        if all(isinstance(r, list) for r in data):
            headers = [str(c).strip() for c in data[0]]
            rows = [[str(c).strip() for c in r] for r in data[1:]]
            return headers, rows
        return ["value"], [[str(x)] for x in data]

    if not isinstance(data, dict):
        return [], []

    headers = data.get("headers") or data.get("columns") or data.get("fields") or []
    rows = data.get("rows") or data.get("data") or data.get("records") or []

    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
        if not headers:
            headers = []
            for row in rows:
                for k in row.keys():
                    if str(k) not in headers:
                        headers.append(str(k))
        headers = [str(h).strip() for h in headers]
        return headers, [[str(r.get(h, "")).strip() for h in headers] for r in rows]

    headers = [str(h).strip() for h in headers] if isinstance(headers, list) else []
    out_rows: list[list[str]] = []
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, list):
                out_rows.append([str(c).strip() for c in r])
            elif isinstance(r, dict):
                if not headers:
                    headers = [str(k) for k in r.keys()]
                out_rows.append([str(r.get(h, "")).strip() for h in headers])
            else:
                out_rows.append([str(r).strip()])
    return headers, out_rows


def _default_spreadsheet_path(pdf_path: str, ext: str = ".xlsx") -> str:
    stem = Path(pdf_path).stem or "extracted"
    safe = re.sub(r"[^\w\-]+", "_", stem).strip("_") or "extracted"
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    return str(DOCUMENTS_DIR / f"{safe}_extracted{ext}")


def write_spreadsheet(
    headers: list | str = "",
    rows: list | str = "",
    path: str = "",
    open_after: bool = True,
) -> dict:
    """
    Write a table to a spreadsheet file (.xlsx preferred, .csv fallback).
    headers: list of column names (or JSON string)
    rows: list of row lists/dicts (or JSON string)
    Returns: { success, path, row_count, headers, opened, message }
    """
    if isinstance(headers, str) and headers.strip().startswith(("[", "{")):
        parsed = _extract_json_object(headers)
        if isinstance(parsed, list):
            headers = parsed
    if isinstance(rows, str) and rows.strip().startswith(("[", "{")):
        parsed = _extract_json_object(rows)
        if parsed is not None:
            rows = parsed

    if isinstance(rows, dict):
        h2, r2 = _normalize_table(rows)
        headers = headers or h2
        rows = r2
    elif isinstance(rows, list) and rows and isinstance(rows[0], dict):
        h2, r2 = _normalize_table(rows)
        headers = headers or h2
        rows = r2

    header_list = [str(h).strip() for h in (headers or [])]
    row_list: list[list[str]] = []
    for r in (rows or []):
        if isinstance(r, list):
            row_list.append([str(c).strip() for c in r])
        else:
            row_list.append([str(r).strip()])

    if not header_list and not row_list:
        return {"success": False, "message": "No headers or rows to write"}

    if not header_list and row_list:
        width = max(len(r) for r in row_list)
        header_list = [f"Column {i + 1}" for i in range(width)]

    # Pad / trim rows to header width
    width = len(header_list)
    normalized = []
    for r in row_list:
        if len(r) < width:
            r = r + [""] * (width - len(r))
        normalized.append(r[:width])

    target = (path or "").strip()
    if not target:
        target = str(DOCUMENTS_DIR / "extracted_data.xlsx")
    expanded = str(Path(target).expanduser().resolve())
    if not expanded.startswith(HOME):
        return {"success": False, "message": "Access denied: outside home directory"}

    Path(expanded).parent.mkdir(parents=True, exist_ok=True)
    ext = Path(expanded).suffix.lower()

    try:
        if ext in (".xlsx", ".xlsm", "") or ext == ".xls":
            if not ext:
                expanded = expanded + ".xlsx"
                ext = ".xlsx"
            if ext == ".xls":
                expanded = str(Path(expanded).with_suffix(".xlsx"))
                ext = ".xlsx"
            try:
                from openpyxl import Workbook

                wb = Workbook()
                ws = wb.active
                ws.title = "Extracted"
                ws.append(header_list)
                for r in normalized:
                    ws.append(r)
                wb.save(expanded)
            except ImportError:
                # Fall back to CSV next to requested path
                expanded = str(Path(expanded).with_suffix(".csv"))
                ext = ".csv"
                with open(expanded, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(header_list)
                    writer.writerows(normalized)
        else:
            # csv / tsv / anything else → CSV
            if ext not in (".csv", ".tsv"):
                expanded = str(Path(expanded).with_suffix(".csv"))
            delim = "\t" if expanded.lower().endswith(".tsv") else ","
            with open(expanded, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f, delimiter=delim)
                writer.writerow(header_list)
                writer.writerows(normalized)
    except Exception as e:
        return {"success": False, "message": f"Could not write spreadsheet: {e}"}

    opened = False
    open_msg = ""
    if open_after:
        try:
            from agent import platform as plat

            open_res = plat.open_path(expanded)
            opened = bool(open_res.get("success"))
            open_msg = open_res.get("message", "")
        except Exception as e:
            open_msg = str(e)

    msg = f"Wrote {len(normalized)} row(s) to {expanded}"
    if opened:
        msg += " (opened)"
    elif open_after and open_msg:
        msg += f" — open note: {open_msg}"

    log.info("Spreadsheet written: %s (%d rows)", expanded, len(normalized))
    return {
        "success": True,
        "path": expanded,
        "row_count": len(normalized),
        "headers": header_list,
        "opened": opened,
        "message": msg,
    }


def extract_pdf_to_spreadsheet(
    pdf_path: str = "",
    query: str = "",
    fields: str = "",
    output_path: str = "",
    open_after: bool = True,
    path: str = "",
) -> dict:
    """
    Locate information in a PDF and write it into a spreadsheet.

    pdf_path / path: PDF file to read
    query: what to look for (e.g. "pricing", "all contact names and emails")
    fields: optional comma-separated column names to force
    output_path: where to save (.xlsx or .csv); defaults under ~/Documents

    Returns: {
      success, path, row_count, headers, rows, pdf_path, query, opened, message
    }
    """
    pdf_path = (pdf_path or path or "").strip()
    if not pdf_path:
        return {"success": False, "message": "No PDF path provided"}

    pdf = read_pdf(pdf_path)
    if not pdf.get("success"):
        return pdf

    text = (pdf.get("text") or "").strip()
    if not text:
        return {"success": False, "message": "PDF has no extractable text"}

    field_list = [f.strip() for f in (fields or "").split(",") if f.strip()]
    what = (query or "").strip() or (
        "Extract all useful structured facts, names, dates, amounts, and key-value pairs"
        if not field_list
        else f"Extract values for these fields: {', '.join(field_list)}"
    )

    columns_hint = (
        f"Use exactly these column headers: {json.dumps(field_list)}.\n"
        if field_list
        else "Choose clear, short column headers that fit the data.\n"
    )

    prompt = (
        "You extract tabular data from a document for a spreadsheet.\n"
        "Return ONLY valid JSON in this exact shape:\n"
        '{"headers":["Col1","Col2"],"rows":[["v1","v2"],["v3","v4"]]}\n'
        "Rules:\n"
        "- One logical record per row\n"
        "- Use empty string when a value is missing\n"
        "- Do not invent facts that are not in the document\n"
        "- Keep cell values short\n"
        f"{columns_hint}"
        f"Task: {what}\n\n"
        f"Document:\n{text[:MAX_CHARS]}"
    )

    try:
        raw = llm_cfg.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            num_predict=800,
            format="json",
        )
    except Exception as e:
        # Older Ollama / model may reject format=json — retry without it
        log.warning("JSON-format chat failed (%s); retrying plain", e)
        try:
            raw = llm_cfg.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                num_predict=800,
            )
        except Exception as e2:
            return {"success": False, "message": f"Extraction LLM failed: {e2}"}

    parsed = _extract_json_object(raw)
    headers, rows = _normalize_table(parsed)

    # If forced fields but model ignored them, rebuild columns
    if field_list and headers != field_list:
        # Map by case-insensitive name when possible
        lower_map = {h.lower(): i for i, h in enumerate(headers)}
        remapped = []
        for r in rows:
            new_r = []
            for f in field_list:
                idx = lower_map.get(f.lower())
                new_r.append(r[idx] if idx is not None and idx < len(r) else "")
            remapped.append(new_r)
        # If no usable mapping and we have dict-like failure, keep original rows padded
        if any(any(c for c in r) for r in remapped):
            headers, rows = field_list, remapped
        else:
            headers = field_list

    if not headers and not rows:
        # Last resort: put whole answer as a single cell so the user still gets a file
        snippet = (raw or "").strip()[:500]
        if not snippet:
            return {
                "success": False,
                "message": "Could not extract structured data from the PDF",
                "raw": raw,
            }
        headers = ["Extracted"]
        rows = [[snippet]]

    out = (output_path or "").strip()
    if not out:
        out = _default_spreadsheet_path(pdf["path"], ext=".xlsx")

    written = write_spreadsheet(
        headers=headers,
        rows=rows,
        path=out,
        open_after=open_after,
    )
    if not written.get("success"):
        return written

    return {
        "success": True,
        "path": written["path"],
        "row_count": written["row_count"],
        "headers": written["headers"],
        "rows": rows,
        "pdf_path": pdf["path"],
        "query": what,
        "opened": written.get("opened", False),
        "message": (
            f"Located info in PDF → {written['row_count']} row(s) saved to "
            f"{written['path']}"
        ),
    }
