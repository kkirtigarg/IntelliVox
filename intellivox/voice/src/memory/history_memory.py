"""
src/memory/history_memory.py
Execution history — successful tool calls only. Backs the "repeat" meta-command.
"""
import time

from .models import HistoryEntry


class HistoryMemory:
    def __init__(self):
        self.entries: list[HistoryEntry] = []

    def record(self, tool: str, args: dict, result: dict) -> None:
        self.entries.append(HistoryEntry(tool=tool, args=dict(args), result=result, ts=time.time()))

    def last(self) -> HistoryEntry | None:
        return self.entries[-1] if self.entries else None
