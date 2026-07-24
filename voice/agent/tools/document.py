"""
agent/tools/document.py
Document understanding tools: read PDFs, summarize content,
compare two document instances, answer questions.
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("intellivox.document")

HOME = str(Path.home())
MAX_CHARS = 12_000   # limit fed to LLM to avoid token overflow
SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "samples"
DEFAULT_DUMMY_PATH = str(SAMPLES_DIR / "dummy_document.txt")

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
        import ollama
        response = ollama.chat(
            model="llama3.1",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3},
        )
        summary = response["message"]["content"].strip()
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
        import ollama
        response = ollama.chat(
            model="llama3.1",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3},
        )
        summary = response["message"]["content"].strip()
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
        import ollama
        response = ollama.chat(
            model="llama3.1",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2},
        )
        return {"success": True, "answer": response["message"]["content"].strip()}
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
        import ollama
        response = ollama.chat(
            model="llama3.1",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3},
        )
        summary = response["message"]["content"].strip()
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
