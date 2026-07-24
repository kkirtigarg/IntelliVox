"""
agent/tools/files.py
File system tools: read, write, list, move, delete, find,
and organize files according to a spoken requirement.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

from agent import llm as llm_cfg
from agent import platform as plat

log = logging.getLogger("intellivox.files")

HOME = plat.HOME

SAFE_BASE_DIRS = [
    os.path.join(HOME, "Desktop"),
    os.path.join(HOME, "Documents"),
    os.path.join(HOME, "Downloads"),
    HOME,
]

# Extension → folder name for "organize by type"
TYPE_FOLDERS: dict[str, str] = {
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".gif": "Images",
    ".webp": "Images", ".bmp": "Images", ".svg": "Images", ".heic": "Images",
    ".tif": "Images", ".tiff": "Images",
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
    ".txt": "Documents", ".md": "Documents", ".rtf": "Documents", ".odt": "Documents",
    ".xls": "Spreadsheets", ".xlsx": "Spreadsheets", ".csv": "Spreadsheets",
    ".ods": "Spreadsheets",
    ".ppt": "Presentations", ".pptx": "Presentations", ".odp": "Presentations",
    ".mp4": "Videos", ".mkv": "Videos", ".avi": "Videos", ".mov": "Videos",
    ".webm": "Videos", ".m4v": "Videos",
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".aac": "Audio",
    ".ogg": "Audio", ".m4a": "Audio",
    ".zip": "Archives", ".tar": "Archives", ".gz": "Archives", ".rar": "Archives",
    ".7z": "Archives", ".bz2": "Archives",
    ".py": "Code", ".js": "Code", ".ts": "Code", ".jsx": "Code", ".tsx": "Code",
    ".java": "Code", ".c": "Code", ".cpp": "Code", ".h": "Code", ".go": "Code",
    ".rs": "Code", ".html": "Code", ".css": "Code", ".json": "Code", ".xml": "Code",
    ".sh": "Code", ".yml": "Code", ".yaml": "Code",
}

KNOWN_DIR_ALIASES = {
    "desktop": "Desktop",
    "downloads": "Downloads",
    "download": "Downloads",
    "documents": "Documents",
    "docs": "Documents",
    "pictures": "Pictures",
    "photos": "Pictures",
    "music": "Music",
    "videos": "Videos",
    "home": "",
}


def find_file(name: str, directory: str = None, ext: str = "") -> dict:
    """
    Search for a file by name (Spotlight on macOS, find/locate on Linux).
    Returns the best match path. Optional ext filters by extension (e.g. "pdf").
    """
    return plat.find_file(name, directory, ext=ext)


def _is_safe_path(path: str) -> bool:
    """Check that path is within an allowed directory."""
    abs_path = str(Path(path).expanduser().resolve())
    return any(abs_path.startswith(base) for base in SAFE_BASE_DIRS)


def list_files(directory: str = "~/Desktop") -> dict:
    """List files in a directory."""
    expanded = str(Path(directory).expanduser())
    if not os.path.isdir(expanded):
        return {"success": False, "message": f"Not a directory: {directory}"}
    files = os.listdir(expanded)
    return {"success": True, "files": sorted(files), "directory": expanded}


def read_file(path: str) -> dict:
    """Read text content of a file."""
    expanded = str(Path(path).expanduser())
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}
    if not _is_safe_path(expanded):
        return {"success": False, "message": "Access denied: outside allowed directories"}
    try:
        with open(expanded, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"success": True, "content": content, "path": expanded}
    except Exception as e:
        return {"success": False, "message": str(e)}


def write_file(path: str, content: str) -> dict:
    """Write text content to a file. REQUIRES user confirmation (handled by safety layer)."""
    expanded = str(Path(path).expanduser())
    if not _is_safe_path(expanded):
        return {"success": False, "message": "Access denied: outside allowed directories"}
    try:
        Path(expanded).parent.mkdir(parents=True, exist_ok=True)
        with open(expanded, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "message": f"Written to {expanded}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def delete_file(path: str) -> dict:
    """Delete a file. REQUIRES user confirmation (handled by safety layer)."""
    expanded = str(Path(path).expanduser())
    if not _is_safe_path(expanded):
        return {"success": False, "message": "Access denied: outside allowed directories"}
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}
    try:
        if os.path.isdir(expanded):
            shutil.rmtree(expanded)
        else:
            os.remove(expanded)
        return {"success": True, "message": f"Deleted: {expanded}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def move_file(src: str, dst: str) -> dict:
    """Move or rename a file. REQUIRES user confirmation."""
    src_exp = str(Path(src).expanduser())
    dst_exp = str(Path(dst).expanduser())
    if not _is_safe_path(src_exp) or not _is_safe_path(dst_exp):
        return {"success": False, "message": "Access denied: outside allowed directories"}
    try:
        shutil.move(src_exp, dst_exp)
        return {"success": True, "message": f"Moved {src} → {dst}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def open_file(path: str) -> dict:
    """Open a file with its default application."""
    return plat.open_path(path)


def resolve_known_directory(name: str) -> str | None:
    """Map spoken folder names (Downloads, Desktop, …) to absolute paths."""
    raw = (name or "").strip().rstrip("/")
    if not raw:
        return None
    # Absolute / home-relative paths — preserve case
    if raw.startswith("~") or raw.startswith("/"):
        return str(Path(raw).expanduser())

    # "Documents/PDFs" → resolve head alias, keep tail as subpath
    parts = [p for p in re.split(r"[/\\]+", raw) if p]
    if not parts:
        return None
    head = re.sub(r"\b(folder|directory|dir)\b", "", parts[0].lower()).strip()
    if head in KNOWN_DIR_ALIASES:
        rel = KNOWN_DIR_ALIASES[head]
        root = str(Path(HOME) / rel) if rel else HOME
        if len(parts) > 1:
            safe_tail = [_folder_label(p) for p in parts[1:]]
            return str(Path(root).joinpath(*safe_tail))
        return root

    key = raw.lower()
    key = re.sub(r"\b(folder|directory|dir)\b", "", key).strip()
    if key in KNOWN_DIR_ALIASES:
        rel = KNOWN_DIR_ALIASES[key]
        return str(Path(HOME) / rel) if rel else HOME
    return None


def _safe_folder_name(name: str) -> str:
    """Sanitize a folder label for filesystem use."""
    cleaned = re.sub(r"[^\w\s\-.]+", "", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or "Other"


_COMMON_FOLDER_LABELS = {
    "pdf": "PDFs", "pdfs": "PDFs",
    "image": "Images", "images": "Images",
    "photo": "Photos", "photos": "Photos",
    "picture": "Pictures", "pictures": "Pictures",
    "video": "Videos", "videos": "Videos",
    "audio": "Audio", "music": "Music",
    "doc": "Documents", "docs": "Documents", "document": "Documents", "documents": "Documents",
    "spreadsheet": "Spreadsheets", "spreadsheets": "Spreadsheets",
    "archive": "Archives", "archives": "Archives", "zip": "Archives", "zips": "Archives",
    "screenshot": "Screenshots", "screenshots": "Screenshots",
    "invoice": "Invoices", "invoices": "Invoices",
    "other": "Other",
}


def _folder_label(name: str) -> str:
    """Nice folder name: PDFs, Images, … or Title Case."""
    key = (name or "").strip().lower()
    if key in _COMMON_FOLDER_LABELS:
        return _COMMON_FOLDER_LABELS[key]
    safe = _safe_folder_name(name)
    return safe.title() if safe == safe.lower() else safe


def _unique_dest(dest: Path) -> Path:
    """Avoid overwriting: file.txt → file_1.txt, file_2.txt, …"""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    parent = dest.parent
    for i in range(1, 1000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem}_{os.getpid()}{suffix}"


def _list_organizable(directory: str) -> dict:
    """List files (not dirs) eligible for organization."""
    expanded = str(Path(directory).expanduser().resolve())
    if not os.path.isdir(expanded):
        return {"success": False, "message": f"Not a directory: {directory}"}
    if not _is_safe_path(expanded):
        return {"success": False, "message": "Access denied: outside allowed directories"}

    items = []
    for name in sorted(os.listdir(expanded)):
        if name.startswith("."):
            continue
        full = os.path.join(expanded, name)
        if os.path.isfile(full):
            items.append({"name": name, "path": full, "ext": Path(name).suffix.lower()})
    return {"success": True, "directory": expanded, "files": items, "count": len(items)}


def _heuristic_plan_moves(files: list[dict], instruction: str, base: str) -> list[dict]:
    """
    Deterministic organization plans for common spoken requests.
    Returns list of {src, dest_folder, dest_path, reason}.
    """
    low = (instruction or "").lower()
    moves: list[dict] = []

    # "put/move all PDFs into Documents/PDFs" / "move images to Pictures"
    m = re.search(
        r"(?:put|move|send|copy)\s+(?:all\s+)?(?:the\s+)?"
        r"(pdfs?|images?|photos?|pictures?|videos?|music|audio|spreadsheets?|docs?|documents?|zips?|archives?)"
        r".*?\b(?:in(?:to)?|to)\b\s+(?:the\s+|my\s+)?([~\w\-./ ]+)",
        low,
    )
    if m:
        kind = m.group(1).rstrip("s")
        dest_spoken = m.group(2).strip(" .,")
        ext_map = {
            "pdf": {".pdf"},
            "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic"},
            "photo": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"},
            "picture": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"},
            "video": {".mp4", ".mkv", ".avi", ".mov", ".webm"},
            "music": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
            "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
            "spreadsheet": {".xls", ".xlsx", ".csv", ".ods"},
            "doc": {".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".pdf"},
            "document": {".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".pdf"},
            "zip": {".zip", ".tar", ".gz", ".rar", ".7z"},
            "archive": {".zip", ".tar", ".gz", ".rar", ".7z"},
        }
        exts = ext_map.get(kind, set())
        dest_root = resolve_known_directory(dest_spoken)
        if dest_root is None:
            # Relative folder under the source directory
            dest_root = str(Path(base) / _folder_label(dest_spoken))
        for f in files:
            if f["ext"] in exts:
                folder = dest_root
                dest = _unique_dest(Path(folder) / f["name"])
                moves.append({
                    "src": f["path"],
                    "dest_folder": folder,
                    "dest_path": str(dest),
                    "reason": f"match {kind} → {Path(folder).name}",
                })
        return moves

    # "by extension" → one folder per extension (.pdf → PDF, .png → PNG)
    if re.search(r"\bby\s+extension", low):
        for f in files:
            ext = f["ext"] or ".noext"
            folder_name = ext.lstrip(".").upper() or "NOEXT"
            folder = str(Path(base) / folder_name)
            dest = _unique_dest(Path(folder) / f["name"])
            moves.append({
                "src": f["path"],
                "dest_folder": folder,
                "dest_path": str(dest),
                "reason": f"extension {ext}",
            })
        return moves

    # Default / "by type" / "by file type"
    # Only use type buckets when the user asked for type-based sorting,
    # or gave no custom rule beyond "organize/sort/tidy".
    custom_hint = re.search(
        r"\b(into|named|called|screenshot|invoice|project|work|personal|"
        r"receipt|photo|image folder)\b",
        low,
    )
    by_type = (
        re.search(r"\bby\s+(file\s+)?type\b", low)
        or (
            not custom_hint
            and (
                not instruction.strip()
                or re.search(r"\b(organise|organize|sort|tidy|arrange|clean)\b", low)
            )
        )
    )
    if by_type:
        for f in files:
            folder_name = TYPE_FOLDERS.get(f["ext"], "Other")
            folder = str(Path(base) / folder_name)
            dest = _unique_dest(Path(folder) / f["name"])
            moves.append({
                "src": f["path"],
                "dest_folder": folder,
                "dest_path": str(dest),
                "reason": f"type → {folder_name}",
            })
        return moves

    return moves


def _llm_plan_moves(files: list[dict], instruction: str, base: str) -> list[dict]:
    """Ask the LLM how to organize files given a free-form spoken rule."""
    listing = "\n".join(f"- {f['name']}" for f in files[:200])
    prompt = (
        "You organize files into folders based on the user's spoken instruction.\n"
        "Return ONLY valid JSON:\n"
        '{"moves":[{"file":"exact-filename.ext","folder":"FolderName"}]}\n'
        "Rules:\n"
        "- folder is a simple name under the same directory (or a known path like Documents/PDFs)\n"
        "- Only use filenames from the list exactly\n"
        "- Skip files that should stay put\n"
        "- Do not invent filenames\n"
        f"Instruction: {instruction}\n"
        f"Directory: {base}\n"
        f"Files:\n{listing}"
    )
    try:
        raw = llm_cfg.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            num_predict=700,
            format="json",
        )
    except Exception as e:
        log.warning("organize_files LLM failed: %s", e)
        return []

    from agent.tools.document import _extract_json_object

    data = _extract_json_object(raw) or {}
    raw_moves = data.get("moves") if isinstance(data, dict) else data
    if not isinstance(raw_moves, list):
        return []

    by_name = {f["name"]: f for f in files}
    planned: list[dict] = []
    for item in raw_moves:
        if not isinstance(item, dict):
            continue
        fname = str(item.get("file") or item.get("name") or "").strip()
        folder = str(item.get("folder") or item.get("destination") or "").strip()
        if not fname or fname not in by_name or not folder:
            continue
        # Absolute / home-relative destination vs subfolder under base
        if folder.startswith(("~", "/")) or folder.lower().split("/")[0] in KNOWN_DIR_ALIASES:
            first = folder.split("/")[0]
            known = resolve_known_directory(first)
            if known and "/" in folder:
                rest = "/".join(folder.split("/")[1:])
                dest_folder = str(Path(known) / rest) if rest else known
            elif known:
                dest_folder = known
            else:
                dest_folder = str(Path(folder).expanduser())
        else:
            dest_folder = str(Path(base) / _folder_label(folder))
        if not _is_safe_path(dest_folder):
            continue
        src = by_name[fname]["path"]
        dest = _unique_dest(Path(dest_folder) / fname)
        # Skip no-op (already in place)
        if str(Path(src).resolve()) == str(dest.resolve()):
            continue
        planned.append({
            "src": src,
            "dest_folder": dest_folder,
            "dest_path": str(dest),
            "reason": f"instruction → {Path(dest_folder).name}",
        })
    return planned


def organize_files(
    directory: str = "~/Downloads",
    instruction: str = "",
    dry_run: bool = False,
    open_after: bool = False,
) -> dict:
    """
    Organize files in a directory according to a spoken requirement.

    Examples of instruction:
      - "by file type"
      - "by extension"
      - "put all PDFs into Documents/PDFs"
      - "sort screenshots into Screenshots and invoices into Invoices"

    directory: folder to organize (Downloads / Desktop / Documents / path)
    dry_run: if True, only report planned moves without changing anything
    """
    raw_dir = (directory or "~/Downloads").strip()
    known = resolve_known_directory(raw_dir)
    expanded = str(Path(known or raw_dir).expanduser().resolve())
    if os.path.isfile(expanded):
        expanded = str(Path(expanded).parent)

    listing = _list_organizable(expanded)
    if not listing.get("success"):
        return listing

    files = listing["files"]
    if not files:
        return {
            "success": True,
            "directory": expanded,
            "moved": 0,
            "moves": [],
            "message": f"No files to organize in {expanded}",
        }

    instruction = (instruction or "").strip()
    moves = _heuristic_plan_moves(files, instruction, expanded)

    # Custom spoken rules not covered by heuristics → LLM, then type fallback
    if not moves and instruction:
        llm_moves = _llm_plan_moves(files, instruction, expanded)
        if llm_moves:
            moves = llm_moves

    if not moves:
        moves = _heuristic_plan_moves(files, "by type", expanded)

    # Deduplicate by src
    seen = set()
    unique_moves = []
    for m in moves:
        if m["src"] in seen:
            continue
        seen.add(m["src"])
        unique_moves.append(m)
    moves = unique_moves

    if dry_run:
        preview = [f"{Path(m['src']).name} → {m['dest_folder']}" for m in moves[:30]]
        return {
            "success": True,
            "directory": expanded,
            "moved": 0,
            "planned": len(moves),
            "moves": moves,
            "dry_run": True,
            "message": f"Dry run: would move {len(moves)} files.\n" + "\n".join(preview),
        }

    done = []
    errors = []
    for m in moves:
        try:
            Path(m["dest_folder"]).mkdir(parents=True, exist_ok=True)
            if not _is_safe_path(m["src"]) or not _is_safe_path(m["dest_path"]):
                errors.append(f"Skipped (unsafe path): {m['src']}")
                continue
            shutil.move(m["src"], m["dest_path"])
            done.append({
                "src": m["src"],
                "dest": m["dest_path"],
                "folder": m["dest_folder"],
                "reason": m.get("reason", ""),
            })
        except Exception as e:
            errors.append(f"{Path(m['src']).name}: {e}")

    opened = False
    if open_after and done:
        try:
            open_res = plat.open_path(expanded)
            opened = bool(open_res.get("success"))
        except Exception:
            pass

    folders = sorted({Path(d["folder"]).name for d in done})
    msg = f"Organized {len(done)} file(s) in {expanded}"
    if folders:
        msg += f" into: {', '.join(folders)}"
    if errors:
        msg += f" ({len(errors)} skipped/failed)"

    log.info("organize_files: %s", msg)
    return {
        "success": True,
        "directory": expanded,
        "moved": len(done),
        "moves": done,
        "errors": errors,
        "folders": folders,
        "opened": opened,
        "instruction": instruction,
        "message": msg,
    }
