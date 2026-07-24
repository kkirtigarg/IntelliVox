"""
src/connectors/memory_connector.py
Per-WebSocket-session registry for ContextManager instances. agent/orchestrator.py
creates one on connect and drops it on disconnect — nothing else needs to know
this registry exists.
"""
from src.memory.context_manager import ContextManager

_sessions: dict[str, ContextManager] = {}


def get_or_create_session(session_id: str) -> ContextManager:
    if session_id not in _sessions:
        _sessions[session_id] = ContextManager()
    return _sessions[session_id]


def remove_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
