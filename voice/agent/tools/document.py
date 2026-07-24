"""
agent/tools/document.py
Document understanding tools: read PDFs, summarize content, answer questions.
"""
import logging
import os
import re
from pathlib import Path

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
