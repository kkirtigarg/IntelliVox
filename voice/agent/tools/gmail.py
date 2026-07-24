"""
agent/tools/gmail.py
Gmail in Google Chrome — uses the user's logged-in Chrome profile.
Reading: tries Chrome JavaScript first, then clipboard fallback (Cmd+A/C).
Optional: Chrome → View → Developer → Allow JavaScript from Apple Events
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
import urllib.parse

from .browser import navigate_url

log = logging.getLogger("intellivox.gmail")

GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"
BODY_LIMIT = 12_000
READ_TIMEOUT = 45


def _gmail_url(query: str = "", recent: bool = True) -> str:
    q = (query or "").strip()
    if q and not recent:
        return f"https://mail.google.com/mail/u/0/#search/{urllib.parse.quote_plus(q)}"
    return GMAIL_INBOX


def open_gmail(query: str = "", browser: str = "chrome", recent: bool = True) -> dict:
    """Open Gmail inbox (or search) in Google Chrome — reuses the active tab."""
    url = _gmail_url(query, recent)
    result = navigate_url(url, browser, new_tab=False)
    if result.get("success"):
        result["url"] = url
        result["message"] = "Opened Gmail in Google Chrome"
    return result


def _read_inbox_js(limit: int) -> str:
    lim = max(1, min(int(limit), 10))
    return (
        "(function(){"
        f"var lim={lim};"
        "var rows=document.querySelectorAll('tr.zA');"
        "var p=[];"
        "for(var i=0;i<Math.min(lim,rows.length);i++){"
        "var t=(rows[i].innerText||'').trim();"
        "if(t)p.push('--- Email '+(p.length+1)+' ---\\n'+t);"
        "}"
        "if(p.length)return p.join('\\n\\n');"
        "return (document.body.innerText||'').substring(0,12000);"
        "})()"
    )


def _read_via_javascript(app: str, limit: int) -> tuple[str, str | None]:
    js = _read_inbox_js(limit).replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "{app}"
        activate
        if (count of windows) is 0 then return "ERROR:NO_WINDOW"
        try
            set pageText to execute front window's active tab javascript "{js}"
            return pageText
        on error errMsg
            return "ERROR:" & errMsg
        end try
    end tell
    '''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=READ_TIMEOUT,
    )
    if result.returncode != 0:
        return "", (result.stderr or "AppleScript failed").strip()
    raw = (result.stdout or "").strip()
    if raw.startswith("ERROR:"):
        return "", raw[6:]
    return raw, None


def _read_via_clipboard(limit: int) -> tuple[str, str | None]:
    """Copy visible Gmail page text when Chrome JS-from-AppleScript is disabled."""
    script = '''
    tell application "Google Chrome" to activate
    delay 0.8
    tell application "System Events"
        keystroke "a" using command down
        delay 0.3
        keystroke "c" using command down
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return "", "Clipboard read timed out"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        hint = ""
        if "not allowed assistive" in err.lower() or "1002" in err:
            hint = " Grant Accessibility permission for Terminal/Python in System Settings."
        return "", f"Clipboard fallback failed.{hint}"

    pb = subprocess.run(["pbpaste"], capture_output=True, text=True)
    raw = (pb.stdout or "").strip()
    if len(raw) < 20:
        return "", "Clipboard was empty — is Gmail loaded and signed in?"

    return _format_clipboard_inbox(raw, limit), None


def _format_clipboard_inbox(text: str, limit: int) -> str:
    """Try to split copied Gmail inbox into message sections."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return text[:BODY_LIMIT]

    sections: list[str] = []
    chunk: list[str] = []
    for ln in lines:
        if re.match(r"^(Inbox|Primary|Promotions|Social|Updates|Gmail|Search)", ln, re.I):
            continue
        if chunk and len(chunk) >= 2 and len(ln) > 60:
            sections.append("\n".join(chunk))
            chunk = [ln]
            if len(sections) >= limit:
                break
            continue
        chunk.append(ln)
        if len(chunk) >= 4:
            sections.append("\n".join(chunk))
            chunk = []
            if len(sections) >= limit:
                break
    if chunk and len(sections) < limit:
        sections.append("\n".join(chunk))

    if len(sections) >= 2:
        return "\n\n".join(
            f"--- Email {i + 1} ---\n{sec}" for i, sec in enumerate(sections[:limit])
        )[:BODY_LIMIT]
    return text[:BODY_LIMIT]


def _read_via_apple_mail(limit: int) -> dict:
    """Fallback: read inbox via Mail.app when Chrome cannot expose page text."""
    from .mail import read_mail, search_mail

    log.info("Falling back to Apple Mail.app for inbox content")
    search = search_mail(recent=True, limit=limit)
    if not search.get("count"):
        return {
            "success": False,
            "message": "Could not read Gmail in Chrome and Apple Mail inbox is empty.",
        }
    result = read_mail(index=1, count=limit)
    if result.get("success"):
        result["partial"] = True
        result["source"] = "apple_mail"
        note = (
            "(Read from Apple Mail — Gmail is still open in Chrome. "
            "For direct Gmail reading, enable Chrome → View → Developer → "
            "Allow JavaScript from Apple Events.)\n\n"
        )
        result["body"] = note + result.get("body", "")
    return result


def read_gmail(limit: int = 5, wait: float = 8, browser: str = "chrome") -> dict:
    """
    Read inbox content for summarization.
    1) Chrome JavaScript  2) clipboard  3) Apple Mail.app fallback
    """
    app = "Google Chrome" if browser.lower() == "chrome" else browser
    time.sleep(max(2.0, float(wait)))

    method = "javascript"
    raw = ""
    try:
        raw, js_err = _read_via_javascript(app, limit)
        if js_err:
            log.info("Gmail JS read unavailable (%s), trying clipboard", js_err[:80])
            raw, clip_err = _read_via_clipboard(limit)
            method = "clipboard"
            if clip_err:
                log.info("Clipboard read failed (%s), trying Apple Mail", clip_err[:80])
                mail_result = _read_via_apple_mail(limit)
                if mail_result.get("success"):
                    log.info(
                        "read_gmail via apple_mail: %d chars",
                        len(mail_result.get("body", "")),
                    )
                    return mail_result
                return {
                    "success": False,
                    "message": (
                        "Could not read mail. Enable Chrome → View → Developer → "
                        "'Allow JavaScript from Apple Events', grant Accessibility "
                        "for Terminal/Python, or add your Gmail account to Mail.app."
                    ),
                }
    except subprocess.TimeoutExpired:
        mail_result = _read_via_apple_mail(limit)
        if mail_result.get("success"):
            return mail_result
        return {"success": False, "message": "Reading Gmail timed out."}

    if not raw or len(raw) < 20:
        return {
            "success": False,
            "message": "Gmail page looks empty — sign in to Gmail in Chrome first.",
        }

    body = raw[:BODY_LIMIT]
    email_count = max(1, body.count("--- Email "))
    log.info("read_gmail via %s: %d chars, ~%d messages", method, len(body), email_count)
    return {
        "success": True,
        "body": body,
        "count": email_count,
        "subject": f"{email_count} Gmail message(s)",
        "partial": method == "clipboard",
    }
