"""
agent/tools/files.py
File system tools: read, write, list, move, delete, find.
"""
import os
import shutil
import subprocess
from pathlib import Path

HOME = str(Path.home())

SAFE_BASE_DIRS = [
    os.path.join(HOME, "Desktop"),
    os.path.join(HOME, "Documents"),
    os.path.join(HOME, "Downloads"),
    HOME,
]


def find_file(name: str, directory: str = None) -> dict:
    """
    Search for a file by name using macOS Spotlight (mdfind).
    Much smarter than guessing paths — finds files anywhere on the Mac.
    Returns the best match path.
    """
    # Use Spotlight for instant, full-disk search
    search_dir = directory or HOME
    result = subprocess.run(
        ["mdfind", "-onlyin", search_dir, "-name", name],
        capture_output=True, text=True
    )
    matches = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]

    if not matches:
        # Broaden search to full home directory
        result2 = subprocess.run(
            ["mdfind", "-onlyin", HOME, name],
            capture_output=True, text=True
        )
        matches = [l.strip() for l in result2.stdout.strip().splitlines() if l.strip()]

    if not matches:
        return {"success": False, "message": f"No file found matching '{name}'", "matches": []}

    # Prefer shorter, more direct paths
    matches.sort(key=lambda p: (len(p.split("/")), p))
    return {"success": True, "path": matches[0], "matches": matches[:5]}


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
    """
    Write text content to a file. Auto-detects format from extension.
    Supports: .txt / .md / .csv and any plain text — .docx (real Word document).
    """
    expanded = str(Path(path).expanduser())
    if not _is_safe_path(expanded):
        return {"success": False, "message": "Access denied: outside allowed directories"}

    ext = Path(expanded).suffix.lower()
    Path(expanded).parent.mkdir(parents=True, exist_ok=True)

    if ext == ".docx":
        return _write_docx(expanded, content)
    else:
        try:
            with open(expanded, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "message": f"Written to {expanded}", "path": expanded}
        except Exception as e:
            return {"success": False, "message": str(e)}


def _write_docx(path: str, content: str) -> dict:
    try:
        from docx import Document
    except ImportError:
        return {"success": False, "message": "python-docx not installed. Run: pip install python-docx"}
    try:
        doc = Document()
        for line in content.splitlines():
            doc.add_paragraph(line)
        doc.save(path)
        return {"success": True, "message": f"Written to {path}", "path": path}
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


def edit_file(path: str, old_text: str, new_text: str) -> dict:
    """
    Replace old_text with new_text in any file type (in-place, never overwrites the whole file).
    Supports: .txt, .md, .csv and plain text files — .docx (Word) — .xlsx/.xls (Excel).
    """
    expanded = str(Path(path).expanduser())
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}
    if not _is_safe_path(expanded):
        return {"success": False, "message": "Access denied: outside allowed directories"}

    ext = Path(expanded).suffix.lower()

    if ext in (".docx",):
        return _edit_docx(expanded, old_text, new_text)
    elif ext in (".xlsx", ".xls", ".xlsm"):
        return _edit_excel(expanded, old_text, new_text)
    else:
        return _edit_plaintext(expanded, old_text, new_text)


def _edit_plaintext(path: str, old_text: str, new_text: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if old_text == "":
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n" + new_text)
            return {"success": True, "message": f"Appended text to {path}", "path": path}
        if old_text not in content:
            return {"success": False, "message": f"Text not found in file: {old_text!r}"}
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.replace(old_text, new_text, 1))
        return {"success": True, "message": f"Replaced text in {path}", "path": path}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _edit_docx(path: str, old_text: str, new_text: str) -> dict:
    try:
        from docx import Document
    except ImportError:
        return {"success": False, "message": "python-docx not installed. Run: pip install python-docx"}
    try:
        doc = Document(path)
        replaced = 0

        def _replace_in_para(para):
            """Replace text in a paragraph, handling text split across multiple runs."""
            nonlocal replaced
            if old_text not in para.text:
                return
            # Merge all run text, replace, then put it back into the first run
            full = para.text
            new_full = full.replace(old_text, new_text, 1)
            for i, run in enumerate(para.runs):
                run.text = new_full if i == 0 else ""
            replaced += 1

        for para in doc.paragraphs:
            _replace_in_para(para)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        _replace_in_para(para)

        if replaced == 0:
            return {"success": False, "message": f"Text not found in document: {old_text!r}"}
        doc.save(path)
        return {"success": True, "message": f"Replaced text in {path}", "path": path}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _edit_excel(path: str, old_text: str, new_text: str) -> dict:
    try:
        import openpyxl
    except ImportError:
        return {"success": False, "message": "openpyxl not installed. Run: pip install openpyxl"}
    try:
        wb = openpyxl.load_workbook(path)
        replaced = 0
        old_lower = old_text.lower()
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None:
                        continue
                    cell_str = str(cell.value)
                    if old_lower in cell_str.lower():
                        # Preserve original case by finding the actual substring position
                        idx = cell_str.lower().find(old_lower)
                        cell.value = cell_str[:idx] + new_text + cell_str[idx + len(old_text):]
                        replaced += 1
        if replaced == 0:
            return {"success": False, "message": f"Text not found in spreadsheet: {old_text!r}"}
        wb.save(path)
        wb.close()
        return {"success": True, "message": f"Replaced text in {replaced} cell(s) in {path}", "path": path}
    except Exception as e:
        return {"success": False, "message": str(e)}


def open_file(path: str) -> dict:
    """Open a file with its default application."""
    import subprocess
    expanded = str(Path(path).expanduser())
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}
    result = subprocess.run(["open", expanded], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Opened {expanded}", "path": expanded}
    return {"success": False, "message": result.stderr.strip()}
