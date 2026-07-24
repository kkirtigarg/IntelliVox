"""
agent/tools/document.py
Document understanding tools: read PDFs, summarize content, answer questions.
"""
import logging
import os
import re
from pathlib import Path

from agent.telemetry import track

log = logging.getLogger("intellivox.document")

HOME = str(Path.home())
MAX_CHARS = 12_000   # limit fed to LLM to avoid token overflow


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

@track(name="summarize", project_name="intellivox", tags=["llm"])
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


@track(name="summarize_codebase", project_name="intellivox", tags=["llm"])
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


def save_summary_file(
    summary: str,
    path: str = "",
    filename: str = "",
    directory: str = "~/Desktop",
) -> dict:
    """
    Save a summary to a .txt file in Desktop, Documents, or Downloads.
    Returns: { success, message, path }
    """
    if not summary or not str(summary).strip():
        return {"success": False, "message": "No summary text to save"}

    safe_dirs = [
        os.path.join(HOME, "Desktop"),
        os.path.join(HOME, "Documents"),
        os.path.join(HOME, "Downloads"),
    ]

    if path:
        out = str(Path(path).expanduser())
    else:
        name = filename or "codebase-summary.txt"
        if not name.lower().endswith(".txt"):
            name = f"{name}.txt"
        out = str(Path(directory).expanduser() / name)

    out = str(Path(out).resolve())
    if not any(out.startswith(base) for base in safe_dirs):
        return {
            "success": False,
            "message": "Can only save summary files to Desktop, Documents, or Downloads",
        }

    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(str(summary).strip() + "\n")
        log.info("Saved summary to %s", out)
        return {"success": True, "message": f"Saved summary to {out}", "path": out}
    except OSError as e:
        return {"success": False, "message": f"Could not save file: {e}"}


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
