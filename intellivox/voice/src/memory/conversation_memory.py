"""
src/memory/conversation_memory.py
Plain transcript log — every command spoken, independent of execution success.
Separate from SessionState (which only reflects commands that actually succeeded).
"""
import time

from .models import ConversationTurn


class ConversationMemory:
    def __init__(self):
        self.turns: list[ConversationTurn] = []

    def record(self, command: str) -> None:
        self.turns.append(ConversationTurn(command=command, ts=time.time()))
