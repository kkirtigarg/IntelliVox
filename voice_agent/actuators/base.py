"""Abstract actuator interface.

All concrete backends (real Windows GUI, browser, mock-for-testing) implement
this interface so the orchestrator never depends on a specific backend.
Every method returns a plain dict describing the observable result, which the
Verifier then checks -- actuators report what they *observed*, not a bare
success/fail boolean, so the caller can independently judge success.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Actuator(ABC):
    @abstractmethod
    def open_application(self, app: str) -> dict: ...

    @abstractmethod
    def list_windows(self) -> list[str]: ...

    @abstractmethod
    def focus_window(self, title_contains: str) -> dict: ...

    @abstractmethod
    def screenshot(self) -> dict: ...

    @abstractmethod
    def read_screen_text(self) -> str: ...

    @abstractmethod
    def click(self, x: int, y: int) -> dict: ...

    @abstractmethod
    def type_text(self, text: str) -> dict: ...

    @abstractmethod
    def open_file(self, path: str) -> dict: ...

    @abstractmethod
    def file_read(self, path: str) -> dict: ...

    @abstractmethod
    def file_write(self, path: str, content: str, overwrite: bool) -> dict: ...

    @abstractmethod
    def file_delete(self, path: str) -> dict: ...

    @abstractmethod
    def browser_navigate(self, url: str) -> dict: ...

    @abstractmethod
    def read_page_text(self) -> str: ...

    @abstractmethod
    def click_page_element(self, selector: str) -> dict: ...

    @abstractmethod
    def form_submit(self, url: str, fields: dict) -> dict: ...

    @abstractmethod
    def send_message(self, to: str, subject: str, body: str) -> dict: ...
