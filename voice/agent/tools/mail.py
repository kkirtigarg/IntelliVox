"""
agent/tools/mail.py
Apple Mail integration via AppleScript — no OAuth (uses Mail.app session).
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("intellivox.mail")

_last_search: list[dict] = []
_last_search_recent: bool = False
SEARCH_TIMEOUT = 45
READ_TIMEOUT = 30
BODY_LIMIT = 12000


def _escape_apple(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _run_applescript(script: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_mail_blob(raw: str, query: str, recent: bool) -> dict:
    global _last_search, _last_search_recent
    _last_search_recent = recent
    if not raw:
        _last_search = []
        return {"success": True, "count": 0, "messages": [], "query": query}

    sep = chr(31)
    field = chr(30)
    messages: list[dict] = []
    for i, chunk in enumerate(raw.split(sep), 1):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(field)
        if len(parts) >= 3:
            mid, subj, preview = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            mid, subj, preview = parts[0], parts[1], ""
        else:
            continue
        messages.append({
            "index": i,
            "inbox_index": i if recent else None,
            "id": mid.strip(),
            "subject": subj.strip(),
            "preview": preview.strip(),
        })

    _last_search = messages
    log.info("Mail search %r → %d hits", query or "(recent)", len(messages))
    return {"success": True, "count": len(messages), "messages": messages, "query": query}


def _message_ref(inbox_index: int | None, message_id: str) -> str:
    if inbox_index:
        return f"message {inbox_index} of inbox"
    mid = message_id.strip()
    if mid.isdigit():
        return f"(first message of inbox whose id is {mid})"
    return f'(first message of inbox whose id is "{_escape_apple(mid)}")'


def _extract_body_from_msg_ref(msg_ref: str) -> str:
    """Read plain text / raw source for a Mail message reference."""
    return f'''
    tell application "Mail"
        try
            set msg to {msg_ref}
            set bod to content of msg
            if bod is missing value or bod is "" then
                try
                    set bod to source of msg
                end try
            end if
            if bod is missing value then return ""
            return bod
        on error
            return ""
        end try
    end tell
    '''


def _open_and_extract_body(msg_ref: str) -> str:
    """Open the message in Mail to force IMAP download, then re-read body."""
    return f'''
    tell application "Mail"
        try
            set msg to {msg_ref}
            open msg
            delay 2
            set bod to content of msg
            if bod is missing value or bod is "" then
                try
                    set bod to source of msg
                end try
            end if
            if bod is missing value then return ""
            return bod
        on error
            return ""
        end try
    end tell
    '''


def _extract_metadata(msg_ref: str) -> str:
    """Build summarize-friendly text when Mail.app exposes no body."""
    return f'''
    tell application "Mail"
        try
            set msg to {msg_ref}
            set subj to subject of msg
            set snd to sender of msg
            set dt to date received of msg
            return "Subject: " & subj & linefeed & "From: " & snd & linefeed & "Date: " & (dt as string)
        on error
            return ""
        end try
    end tell
    '''


def _collect_recent_script(lim: int) -> str:
    """Fetch only the newest N inbox messages — O(limit), not O(inbox size)."""
    return f'''
    set _sep to (ASCII character 30)
    set _rec to (ASCII character 31)
    set outLines to {{}}
    tell application "Mail"
        repeat with i from 1 to {lim}
            try
                set msg to message i of inbox
                set mid to id of msg
                set subj to subject of msg
                set bod to content of msg
                if bod is missing value or bod is "" then
                    try
                        set bod to source of msg
                    end try
                end if
                if bod is missing value then set bod to ""
                if (length of bod) > 2000 then
                    set prev to text 1 thru 2000 of bod
                else
                    set prev to bod
                end if
                set end of outLines to (mid as string) & _sep & subj & _sep & prev
            end try
        end repeat
    end tell
    set AppleScript's text item delimiters to _rec
    set blob to outLines as string
    set AppleScript's text item delimiters to ""
    return blob
    '''


def _collect_filter_script(filter_expr: str, lim: int) -> str:
    """Collect up to lim messages matching a Mail 'whose' filter."""
    return f'''
    set _sep to (ASCII character 30)
    set _rec to (ASCII character 31)
    set outLines to {{}}
    set n to 0
    tell application "Mail"
        set src to {filter_expr}
        repeat with msg in src
            if n is greater than or equal to {lim} then exit repeat
            try
                set mid to id of msg
                set subj to subject of msg
                set bod to content of msg
                if bod is missing value or bod is "" then
                    try
                        set bod to source of msg
                    end try
                end if
                if bod is missing value then set bod to ""
                if (length of bod) > 2000 then
                    set prev to text 1 thru 2000 of bod
                else
                    set prev to bod
                end if
                set end of outLines to (mid as string) & _sep & subj & _sep & prev
                set n to n + 1
            end try
        end repeat
    end tell
    set AppleScript's text item delimiters to _rec
    set blob to outLines as string
    set AppleScript's text item delimiters to ""
    return blob
    '''


def search_mail(query: str = "", limit: int = 5, recent: bool = False) -> dict:
    """
    Search Apple Mail inbox.
    recent=True (or empty query): newest messages only — fast.
    Otherwise uses Mail's native 'whose' filter (subject first, then body).
    """
    lim = max(1, min(int(limit), 10))
    q = (query or "").strip()
    is_recent = recent or not q

    try:
        if is_recent:
            script = _collect_recent_script(lim)
            result = _run_applescript(script, SEARCH_TIMEOUT)
            label = "recent"
        else:
            q_esc = _escape_apple(q)
            script = _collect_filter_script(
                f'(messages of inbox whose subject contains "{q_esc}")',
                lim,
            )
            result = _run_applescript(script, SEARCH_TIMEOUT)
            raw = (result.stdout or "").strip()
            if result.returncode == 0 and raw:
                return _parse_mail_blob(raw, q, recent=False)

            script = _collect_filter_script(
                f'(messages of inbox whose content contains "{q_esc}")',
                lim,
            )
            result = _run_applescript(script, SEARCH_TIMEOUT)
            label = q

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "Mail search failed").strip()
            if "Not authorized" in err or "assistive" in err.lower():
                err += " — grant Automation permission for Terminal/Python to control Mail."
            return {"success": False, "message": err}

        return _parse_mail_blob(
            (result.stdout or "").strip(),
            label if is_recent else q,
            recent=is_recent,
        )

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"Mail search timed out after {SEARCH_TIMEOUT}s — inbox may be very large.",
        }


def _run_body_script(script: str) -> str:
    try:
        result = _run_applescript(script, READ_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _read_one_message(idx: int, *, try_open: bool, max_chars: int) -> dict:
    """Read a single cached search result by 1-based index."""
    entry = _last_search[idx - 1]
    subject = entry.get("subject", "")
    preview = (entry.get("preview") or "").strip()
    inbox_index = entry.get("inbox_index") if _last_search_recent else None
    if inbox_index is None and _last_search_recent:
        inbox_index = idx
    msg_ref = _message_ref(inbox_index, str(entry.get("id", "")))

    body = preview
    partial = bool(preview)
    if not body:
        body = _run_body_script(_extract_body_from_msg_ref(msg_ref))
        partial = False
    if not body and try_open:
        body = _run_body_script(_open_and_extract_body(msg_ref))
        partial = False
    if not body:
        meta = _run_body_script(_extract_metadata(msg_ref))
        if meta:
            body = (
                meta
                + "\n\nNote: Mail.app did not expose the message body "
                "(common for HTML-only or not-yet-downloaded mail). "
                "Summarize from the available headers."
            )
            partial = True

    if not body:
        return {"success": False, "subject": subject, "index": idx}

    return {
        "success": True,
        "subject": subject,
        "body": body[:max_chars],
        "index": idx,
        "partial": partial,
    }


def read_mail(index: int = 1, count: int = 1) -> dict:
    """Read message(s) from the last search_mail results (count > 1 reads a batch)."""
    if not _last_search:
        return {"success": False, "message": "Search mail first — no messages cached."}

    start = int(index)
    batch = max(1, min(int(count), 10))
    if start < 1 or start > len(_last_search):
        return {"success": False, "message": f"Invalid message index {start} (have {len(_last_search)})"}

    end = min(start + batch - 1, len(_last_search))
    per_msg_limit = max(500, BODY_LIMIT // batch)

    try:
        if batch == 1:
            one = _read_one_message(start, try_open=True, max_chars=BODY_LIMIT)
            if not one.get("success"):
                subj = one.get("subject", "")
                return {
                    "success": False,
                    "message": (
                        f"Could not read body for '{subj[:60]}'. "
                        "Try opening the message in Mail.app once, then retry."
                    ),
                }
            return one

        sections: list[str] = []
        subjects: list[str] = []
        partial = False
        read_count = 0
        for i in range(start, end + 1):
            one = _read_one_message(i, try_open=False, max_chars=per_msg_limit)
            if not one.get("success"):
                continue
            read_count += 1
            subjects.append(one["subject"])
            partial = partial or bool(one.get("partial"))
            sections.append(
                f"--- Email {read_count} ({one['subject'][:80]}) ---\n{one['body']}"
            )

        if not sections:
            return {
                "success": False,
                "message": f"Could not read any of the {end - start + 1} requested messages.",
            }

        combined = "\n\n".join(sections)[:BODY_LIMIT]
        log.info("Read %d/%d cached mail messages", read_count, end - start + 1)
        return {
            "success": True,
            "subject": subjects[0] if len(subjects) == 1 else f"{len(subjects)} messages",
            "subjects": subjects,
            "body": combined,
            "index": start,
            "count": read_count,
            "partial": partial,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Reading mail timed out."}
