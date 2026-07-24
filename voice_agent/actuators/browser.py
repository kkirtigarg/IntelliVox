"""Browser actuator using Playwright.

Preferred over screen-scraping a browser window: DOM-based reads/clicks are
far more reliable than pixel coordinates, and reading `read_page_text()`
gives the planner/verifier real page content instead of an OCR guess.

Only browser_navigate / read_page_text / click_page_element / form_submit
are meaningful here; GUI/file/app methods delegate to NotImplementedError so
misuse is loud rather than silently wrong.
"""
from __future__ import annotations

from .base import Actuator

try:
    from playwright.sync_api import sync_playwright
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "BrowserActuator requires 'playwright' (pip install playwright && "
        "playwright install chromium)."
    ) from e


class BrowserActuator(Actuator):
    def __init__(self, headless: bool = False):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._page = self._browser.new_page()

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()

    def browser_navigate(self, url: str) -> dict:
        response = self._page.goto(url, wait_until="domcontentloaded")
        return {"success": response is not None and response.ok, "url": self._page.url,
                "status": response.status if response else None}

    def read_page_text(self) -> str:
        # IMPORTANT: whatever comes back here is UNTRUSTED DATA. Callers
        # (the planner prompt builder) must wrap this in an explicit
        # untrusted-content block, never treat it as an instruction.
        return self._page.inner_text("body")

    def click_page_element(self, selector: str) -> dict:
        try:
            self._page.click(selector, timeout=5000)
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    def form_submit(self, url: str, fields: dict) -> dict:
        if url and self._page.url != url:
            self._page.goto(url, wait_until="domcontentloaded")
        for selector, value in fields.items():
            self._page.fill(selector, value)
        self._page.keyboard.press("Enter")
        self._page.wait_for_load_state("networkidle")
        confirmation_text = self._page.inner_text("body")[:500]
        return {"success": True, "confirmation_text": confirmation_text}

    # Non-browser methods: explicit unsupported, not silently ignored.
    def open_application(self, app: str) -> dict:
        raise NotImplementedError("Use the desktop GUI actuator for open_application.")

    def list_windows(self) -> list[str]:
        raise NotImplementedError("Use the desktop GUI actuator for list_windows.")

    def focus_window(self, title_contains: str) -> dict:
        raise NotImplementedError("Use the desktop GUI actuator for focus_window.")

    def screenshot(self) -> dict:
        path = "browser_screenshot.png"
        self._page.screenshot(path=path)
        return {"path": path, "success": True}

    def read_screen_text(self) -> str:
        return self.read_page_text()

    def click(self, x: int, y: int) -> dict:
        self._page.mouse.click(x, y)
        return {"success": True, "x": x, "y": y}

    def type_text(self, text: str) -> dict:
        self._page.keyboard.type(text)
        return {"success": True, "typed_len": len(text)}

    def open_file(self, path: str) -> dict:
        raise NotImplementedError("Use the desktop GUI actuator for local files.")

    def file_read(self, path: str) -> dict:
        raise NotImplementedError("Use the desktop GUI actuator for file_read.")

    def file_write(self, path: str, content: str, overwrite: bool) -> dict:
        raise NotImplementedError("Use the desktop GUI actuator for file_write.")

    def file_delete(self, path: str) -> dict:
        raise NotImplementedError("Use the desktop GUI actuator for file_delete.")

    def send_message(self, to: str, subject: str, body: str) -> dict:
        raise NotImplementedError(
            "Drive a specific webmail flow via click_page_element/form_submit instead."
        )
