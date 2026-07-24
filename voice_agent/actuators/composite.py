"""Composite actuator: desktop GUI for files/windows, Playwright for browser.

Used by `python -m voice_agent.cli --real` so one orchestrator can drive both
without the caller picking a backend per step.
"""
from __future__ import annotations

from .base import Actuator


BROWSER_METHODS = {
    "browser_navigate",
    "read_page_text",
    "click_page_element",
    "form_submit",
}


class CompositeActuator(Actuator):
    def __init__(self, desktop: Actuator, browser: Actuator | None = None):
        self.desktop = desktop
        self.browser = browser

    def _route(self, name: str):
        if name in BROWSER_METHODS and self.browser is not None:
            return getattr(self.browser, name)
        return getattr(self.desktop, name)

    def open_application(self, app: str) -> dict:
        return self._route("open_application")(app)

    def list_windows(self) -> list[str]:
        return self._route("list_windows")()

    def focus_window(self, title_contains: str) -> dict:
        return self._route("focus_window")(title_contains)

    def screenshot(self) -> dict:
        return self._route("screenshot")()

    def read_screen_text(self) -> str:
        return self._route("read_screen_text")()

    def click(self, x: int, y: int) -> dict:
        return self._route("click")(x, y)

    def type_text(self, text: str) -> dict:
        return self._route("type_text")(text)

    def open_file(self, path: str) -> dict:
        return self._route("open_file")(path)

    def file_read(self, path: str) -> dict:
        return self._route("file_read")(path)

    def file_write(self, path: str, content: str, overwrite: bool) -> dict:
        return self._route("file_write")(path, content, overwrite)

    def file_delete(self, path: str) -> dict:
        return self._route("file_delete")(path)

    def browser_navigate(self, url: str, browser: str | None = None) -> dict:
        return self._route("browser_navigate")(url, browser=browser)

    def read_page_text(self) -> str:
        return self._route("read_page_text")()

    def click_page_element(self, selector: str) -> dict:
        return self._route("click_page_element")(selector)

    def form_submit(self, url: str, fields: dict) -> dict:
        return self._route("form_submit")(url, fields)

    def send_message(self, to: str, subject: str, body: str) -> dict:
        return self._route("send_message")(to, subject, body)

    def close(self) -> None:
        closer = getattr(self.browser, "close", None)
        if callable(closer):
            closer()
