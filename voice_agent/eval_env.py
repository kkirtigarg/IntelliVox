"""Participant evaluation desktop environment.

Provides a preconfigured workspace with:
  - common applications (browser, file manager, PDF viewer, text editor,
    spreadsheet, document editor, presentation editor)
  - sample files and predefined task states
  - reset-between-tasks support

Golden (immutable) templates live in environment/golden/.
The live working copy is environment/workspace/ (reset restores from golden).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ENV_ROOT = ROOT / "environment"
GOLDEN_DIR = ENV_ROOT / "golden"
WORKSPACE_DIR = ENV_ROOT / "workspace"
TASKS_PATH = ENV_ROOT / "tasks.yaml"

# Friendly spoken names → launch candidates (Linux). Windows mapping is separate.
EVAL_APPS: dict[str, list[str]] = {
    # web browser
    "browser": ["firefox", "chromium", "google-chrome", "google-chrome-stable"],
    "firefox": ["firefox"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium"],
    "chromium": ["chromium", "chromium-browser"],
    # file manager
    "files": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "file manager": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "explorer": ["thunar", "nautilus", "dolphin", "pcmanfm", "nemo"],
    "thunar": ["thunar"],
    # PDF viewer
    "pdf": ["evince", "atril", "okular", "xreader"],
    "pdf viewer": ["evince", "atril", "okular", "xreader"],
    "evince": ["evince"],
    # text editor
    "notepad": ["mousepad", "gedit", "xed", "kate", "leafpad"],
    "text editor": ["mousepad", "gedit", "xed", "kate"],
    "mousepad": ["mousepad"],
    "gedit": ["gedit"],
    # spreadsheet
    "excel": ["libreoffice", "soffice", "localc"],
    "spreadsheet": ["libreoffice", "soffice", "localc"],
    "calc": ["localc", "libreoffice", "soffice"],
    "libreoffice calc": ["localc", "libreoffice"],
    # document editor
    "word": ["libreoffice", "soffice", "lowriter"],
    "writer": ["lowriter", "libreoffice", "soffice"],
    "document editor": ["lowriter", "libreoffice", "soffice"],
    # presentation editor
    "powerpoint": ["libreoffice", "soffice", "loimpress"],
    "impress": ["loimpress", "libreoffice", "soffice"],
    "presentation": ["loimpress", "libreoffice", "soffice"],
    "presentation editor": ["loimpress", "libreoffice", "soffice"],
    # extras already used by the agent
    "vscode": ["code", "codium"],
    "code": ["code", "codium"],
    "terminal": ["x-terminal-emulator", "xfce4-terminal", "gnome-terminal", "konsole"],
}


@dataclass
class EvalTask:
    id: str
    title: str
    instruction: str
    success_hints: list[str]
    starting_files: list[str]


def _minimal_pdf(text_lines: list[str]) -> bytes:
    """Build a tiny one-page PDF with Helvetica text (no external deps)."""
    # Position lines from top of letter page.
    content_ops = ["BT", "/F1 14 Tf", "50 750 Td", "16 TL"]
    for i, line in enumerate(text_lines):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i == 0:
            content_ops.append(f"({safe}) Tj")
        else:
            content_ops.append("T*")
            content_ops.append(f"({safe}) Tj")
    content_ops.append("ET")
    stream = "\n".join(content_ops).encode("latin-1", errors="replace")

    objs: list[bytes] = []
    objs.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objs.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objs.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
    )
    objs.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode()
        + stream
        + b"\nendstream endobj\n"
    )
    objs.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


def seed_golden(force: bool = False) -> Path:
    """Create the immutable golden sample desktop if missing (or force rewrite)."""
    if GOLDEN_DIR.exists() and not force and any(GOLDEN_DIR.iterdir()):
        return GOLDEN_DIR

    if GOLDEN_DIR.exists() and force:
        shutil.rmtree(GOLDEN_DIR)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    docs = GOLDEN_DIR / "Documents"
    desktop = GOLDEN_DIR / "Desktop"
    downloads = GOLDEN_DIR / "Downloads"
    for d in (docs, desktop, downloads):
        d.mkdir(parents=True, exist_ok=True)

    # Text editor sample
    (docs / "meeting_notes.txt").write_text(
        "Weekly sync — 24 Jul 2026\n"
        "- Follow up with vendor\n"
        "- Confirm invoice total\n"
        "- Prepare slides for Friday\n",
        encoding="utf-8",
    )

    # Spreadsheet sample (CSV — opens in LibreOffice Calc)
    (docs / "budget.csv").write_text(
        "Category,Amount\n"
        "Travel,1200\n"
        "Software,450\n"
        "Hardware,890\n"
        "Total,\n",
        encoding="utf-8",
    )

    # Document editor sample (plain text LibreOffice can open; also .fodt-like name)
    (docs / "draft_letter.txt").write_text(
        "Dear Vendor,\n\n"
        "Please confirm Invoice #4471 for Alpin Auto Parts.\n"
        "Total due should be 8420.\n\n"
        "Regards,\nOperations\n",
        encoding="utf-8",
    )

    # Presentation outline (opens in text editor / Impress can import)
    (docs / "friday_deck_outline.txt").write_text(
        "Slide 1: Title — Q3 Ops Update\n"
        "Slide 2: Invoice status\n"
        "Slide 3: Budget snapshot\n"
        "Slide 4: Next steps\n",
        encoding="utf-8",
    )

    # PDF viewer sample
    pdf = _minimal_pdf([
        "Invoice #4471",
        "Vendor: Alpin Auto Parts",
        "Total Due: 8420",
        "Status: Unpaid",
    ])
    (docs / "invoice.pdf").write_bytes(pdf)

    # Desktop shortcut-style readme for participants
    (desktop / "START_HERE.txt").write_text(
        "Participant desktop environment\n"
        "================================\n\n"
        "Apps available (voice/text):\n"
        "  browser / firefox     — web browser\n"
        "  files / explorer      — file manager\n"
        "  pdf / evince          — PDF viewer\n"
        "  notepad / text editor — text editor\n"
        "  calc / spreadsheet    — LibreOffice Calc\n"
        "  writer / word         — LibreOffice Writer\n"
        "  impress / powerpoint  — LibreOffice Impress\n\n"
        "Sample files are under Documents/.\n"
        "Say or type: reset environment   to restore sample files between tasks.\n",
        encoding="utf-8",
    )

    (downloads / "readme.txt").write_text(
        "Downloads folder — empty at task start unless a task seeds files here.\n",
        encoding="utf-8",
    )

    # Task state marker consumed by reset / task loader
    state = {
        "version": 1,
        "description": "Golden participant desktop snapshot",
        "apps": list(EVAL_APPS.keys()),
        "files": sorted(str(p.relative_to(GOLDEN_DIR)) for p in GOLDEN_DIR.rglob("*") if p.is_file()),
    }
    (GOLDEN_DIR / ".env_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return GOLDEN_DIR


def default_tasks() -> list[dict[str, Any]]:
    return [
        {
            "id": "task_open_invoice",
            "title": "Open the invoice PDF",
            "instruction": "Open the PDF viewer and open Documents/invoice.pdf",
            "success_hints": ["invoice.pdf is open", "PDF viewer focused"],
            "starting_files": ["Documents/invoice.pdf"],
        },
        {
            "id": "task_budget_total",
            "title": "Fill budget total",
            "instruction": (
                "Open the spreadsheet Documents/budget.csv and put the sum of "
                "Amount values into the Total row"
            ),
            "success_hints": ["budget.csv Total cell is 2540"],
            "starting_files": ["Documents/budget.csv"],
        },
        {
            "id": "task_notes_followup",
            "title": "Edit meeting notes",
            "instruction": (
                "Open meeting_notes.txt in the text editor and add a line "
                "'Called vendor about invoice'"
            ),
            "success_hints": ["meeting_notes.txt contains Called vendor"],
            "starting_files": ["Documents/meeting_notes.txt"],
        },
        {
            "id": "task_browser_vendor",
            "title": "Look up vendor in browser",
            "instruction": "Open firefox and go to https://www.google.com",
            "success_hints": ["firefox open", "google.com loaded"],
            "starting_files": [],
        },
        {
            "id": "task_create_app_py",
            "title": "Create app.py in editor",
            "instruction": "Open vscode and create a file with name app.py",
            "success_hints": ["app.py exists in workspace"],
            "starting_files": [],
        },
    ]


def seed_tasks(force: bool = False) -> Path:
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TASKS_PATH.exists() and not force:
        return TASKS_PATH
    TASKS_PATH.write_text(
        yaml.safe_dump({"tasks": default_tasks()}, sort_keys=False),
        encoding="utf-8",
    )
    return TASKS_PATH


def load_tasks() -> list[EvalTask]:
    seed_tasks()
    data = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8")) or {}
    out: list[EvalTask] = []
    for raw in data.get("tasks", []):
        out.append(EvalTask(
            id=str(raw["id"]),
            title=str(raw.get("title", raw["id"])),
            instruction=str(raw.get("instruction", "")),
            success_hints=list(raw.get("success_hints") or []),
            starting_files=list(raw.get("starting_files") or []),
        ))
    return out


def get_task(task_id: str) -> EvalTask | None:
    for t in load_tasks():
        if t.id == task_id:
            return t
    return None


def reset_environment(task_id: str | None = None) -> dict[str, Any]:
    """Restore live workspace from golden snapshot. Optional task priming."""
    seed_golden()
    seed_tasks()

    if WORKSPACE_DIR.exists():
        shutil.rmtree(WORKSPACE_DIR)
    shutil.copytree(GOLDEN_DIR, WORKSPACE_DIR)

    task_meta: dict[str, Any] = {}
    if task_id:
        task = get_task(task_id)
        if task is None:
            raise ValueError(f"Unknown task_id {task_id!r}. Known: {[t.id for t in load_tasks()]}")
        task_meta = {
            "id": task.id,
            "title": task.title,
            "instruction": task.instruction,
            "success_hints": task.success_hints,
        }
        (WORKSPACE_DIR / ".current_task.json").write_text(
            json.dumps(task_meta, indent=2), encoding="utf-8",
        )
    else:
        marker = WORKSPACE_DIR / ".current_task.json"
        if marker.exists():
            marker.unlink()

    return {
        "workspace": str(WORKSPACE_DIR),
        "golden": str(GOLDEN_DIR),
        "task": task_meta or None,
        "files": sorted(
            str(p.relative_to(WORKSPACE_DIR))
            for p in WORKSPACE_DIR.rglob("*")
            if p.is_file() and p.name != ".current_task.json"
        ),
    }


def workspace_path() -> Path:
    """Ensure workspace exists (reset if empty) and return it."""
    if not WORKSPACE_DIR.exists() or not any(WORKSPACE_DIR.iterdir()):
        reset_environment()
    return WORKSPACE_DIR


def eval_app_allowlist() -> tuple[str, ...]:
    """Allowlist entries for PolicyEngine (spoken names + keys)."""
    return tuple(sorted(EVAL_APPS.keys()))
