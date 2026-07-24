"""
src/memory/models.py
Plain data holders for contextual session memory. No behavior here —
just the shape of what gets remembered between voice commands.
"""
from dataclasses import dataclass, field


@dataclass
class VideoState:
    title: str | None = None
    url: str | None = None
    state: str = "none"  # "none" | "playing" | "paused"


@dataclass
class SessionState:
    active_application: str | None = None
    active_browser: str | None = None
    active_window: str | None = None
    current_website: str | None = None
    current_url: str | None = None
    current_tab: str | None = None
    current_page: str | None = None
    current_document: str | None = None
    current_video: VideoState = field(default_factory=VideoState)
    current_file: str | None = None
    selected_element: str | None = None
    previous_action: str | None = None
    previous_command: str | None = None
    completed_tasks: list[str] = field(default_factory=list)
    pending_tasks: list[dict] = field(default_factory=list)


@dataclass
class HistoryEntry:
    tool: str
    args: dict
    result: dict
    ts: float


@dataclass
class ConversationTurn:
    command: str
    ts: float
