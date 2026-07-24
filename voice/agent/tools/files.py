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
    import subprocess
    expanded = str(Path(path).expanduser())
    if not os.path.exists(expanded):
        return {"success": False, "message": f"File not found: {path}"}
    result = subprocess.run(["open", expanded], capture_output=True, text=True)
    if result.returncode == 0:
        return {"success": True, "message": f"Opened {expanded}"}
    return {"success": False, "message": result.stderr.strip()}
