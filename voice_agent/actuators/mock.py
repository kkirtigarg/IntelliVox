"""In-memory mock actuator.

Used by unit tests and by the CLI demo mode, so the full
plan -> policy -> confirm -> execute -> verify -> audit loop can be exercised
without a real Windows display, mouse, or files. Deliberately simulates
realistic observable state (open windows, a tiny fake filesystem, a tiny fake
"page") so the Verifier has something real to check.
"""
from __future__ import annotations

import hashlib
from .base import Actuator


class MockActuator(Actuator):
    def __init__(self):
        self.open_windows: list[str] = []
        self.focused_window: str | None = None
        self.fake_fs: dict[str, str] = {}
        self.fake_page_url: str | None = None
        self.fake_page_text: str = ""
        self.sent_messages: list[dict] = []
        self.submitted_forms: list[dict] = []
        self.action_log: list[dict] = []

    def _record(self, name: str, **kw):
        self.action_log.append({"action": name, **kw})

    def open_application(self, app: str) -> dict:
        title = f"{app} - Main Window"
        self.open_windows.append(title)
        self.focused_window = title
        self._record("open_application", app=app)
        return {"observed_window_title": title, "success": True}

    def list_windows(self) -> list[str]:
        return list(self.open_windows)

    def focus_window(self, title_contains: str) -> dict:
        match = next((w for w in self.open_windows if title_contains.lower() in w.lower()), None)
        if match:
            self.focused_window = match
        self._record("focus_window", title_contains=title_contains, matched=match)
        return {"observed_window_title": match, "success": match is not None}

    def screenshot(self) -> dict:
        content = f"screenshot-of-{self.focused_window}".encode()
        return {"hash": hashlib.sha256(content).hexdigest(), "success": True}

    def read_screen_text(self) -> str:
        return self.focused_window or ""

    def click(self, x: int, y: int) -> dict:
        self._record("click", x=x, y=y)
        return {"success": True, "x": x, "y": y}

    def type_text(self, text: str) -> dict:
        self._record("type_text", text=text)
        return {"success": True, "typed_len": len(text)}

    def open_file(self, path: str) -> dict:
        exists = path in self.fake_fs
        if not exists:
            # simulate file existing on disk even if not "written" via this actuator
            exists = True
            self.fake_fs.setdefault(path, "")
        self._record("open_file", path=path)
        return {"success": exists, "path": path}

    def file_read(self, path: str) -> dict:
        content = self.fake_fs.get(path)
        return {"success": content is not None, "content": content}

    def file_write(self, path: str, content: str, overwrite: bool) -> dict:
        existed = path in self.fake_fs
        if existed and not overwrite:
            return {"success": False, "reason": "file exists and overwrite=False"}
        self.fake_fs[path] = content
        self._record("file_write", path=path, overwrite=overwrite)
        return {"success": True, "path": path, "bytes": len(content)}

    def file_delete(self, path: str) -> dict:
        existed = path in self.fake_fs
        self.fake_fs.pop(path, None)
        self._record("file_delete", path=path)
        return {"success": existed, "path": path}

    def browser_navigate(self, url: str, browser: str | None = None) -> dict:
        self.fake_page_url = url
        self.fake_page_text = f"<simulated content of {url}>"
        self._record("browser_navigate", url=url, browser=browser)
        return {"success": True, "url": url, "browser": browser}

    def read_page_text(self) -> str:
        return self.fake_page_text

    def click_page_element(self, selector: str) -> dict:
        self._record("click_page_element", selector=selector)
        return {"success": True, "selector": selector}

    def form_submit(self, url: str, fields: dict) -> dict:
        self.submitted_forms.append({"url": url, "fields": fields})
        self._record("form_submit", url=url, fields=fields)
        return {"success": True, "confirmation_text": "success"}

    def send_message(self, to: str, subject: str, body: str) -> dict:
        self.sent_messages.append({"to": to, "subject": subject, "body": body})
        self._record("send_message", to=to, subject=subject)
        return {"success": True, "to": to}
