"""
src/connectors/browser_memory_connector.py
Best-effort resolution of "what site/URL is the browser on right now" after a
vision-driven computer_use step. None of the existing tools expose this
directly, so this connector tries progressively weaker sources:

  1. A real address-bar read (macOS: reuses agent.verifier's existing
     AppleScript reader; Windows: an optional UI Automation reader).
  2. A plain-text domain guess pulled from the step's own result message.

Reuses agent.verifier where it already works instead of duplicating it.
"""
import logging
import platform
import re

from agent import verifier

log = logging.getLogger("intellivox.browser_memory_connector")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

try:
    import uiautomation as auto
    UIAUTOMATION_AVAILABLE = True
except Exception:
    UIAUTOMATION_AVAILABLE = False

_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)\b", re.IGNORECASE)


def _read_windows_chrome_address_bar() -> str | None:
    """Best-effort: read Chrome's omnibox text via UI Automation (optional dep)."""
    if not UIAUTOMATION_AVAILABLE:
        return None
    try:
        chrome = auto.WindowControl(searchDepth=2, ClassName="Chrome_WidgetWin_1")
        if not chrome.Exists(0.5):
            return None
        bar = chrome.EditControl(searchDepth=20, NameRegex="Address.*search|Search.*address")
        if bar.Exists(0.5):
            text = (bar.GetValuePattern().Value or "").strip()
            return text or None
    except Exception as e:
        log.debug("Windows address-bar read failed: %s", e)
    return None


def _read_mac_chrome_url() -> str | None:
    try:
        return verifier.get_active_url() or None
    except Exception as e:
        log.debug("macOS address-bar read failed: %s", e)
        return None


def _guess_domain_from_text(text: str) -> str | None:
    if not text:
        return None
    match = _DOMAIN_RE.search(text)
    return match.group(1).lower() if match else None


def resolve_current_page(result: dict) -> tuple[str | None, str | None]:
    """
    Best-effort (site, url) after a computer_use step.
    Returns (None, None) when nothing could be determined — callers must not
    treat that as "left the site", just as "unknown, leave memory as-is".
    """
    url = None
    if IS_MAC:
        url = _read_mac_chrome_url()
    elif IS_WINDOWS:
        url = _read_windows_chrome_address_bar()

    if url:
        site = _guess_domain_from_text(url) or url
        return site, url

    guess = _guess_domain_from_text(result.get("message", ""))
    return guess, None
