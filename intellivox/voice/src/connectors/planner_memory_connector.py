"""
src/connectors/planner_memory_connector.py
Feeds Session Memory into the *existing* agent.planner.plan(transcript,
previous_context) call without touching the planner's prompt or logic —
previous_context was already an unused parameter on plan().
"""
import re

_REFERENCE_WORDS = {"it", "this", "that", "here", "there", "previous", "same", "last"}


def build_context(ctx) -> str:
    """Formats active memory into the plain-text block plan() already accepts."""
    s = ctx.session.state
    lines = []
    if s.active_application:
        lines.append(f"Active application: {s.active_application}")
    if s.active_browser and s.active_browser != s.active_application:
        lines.append(f"Active browser: {s.active_browser}")
    if s.current_website:
        url_part = f" ({s.current_url})" if s.current_url else ""
        lines.append(f"Current website: {s.current_website}{url_part}")
    if s.current_video.title or s.current_video.state != "none":
        title = s.current_video.title or "unknown title"
        lines.append(f"Current video: '{title}' [{s.current_video.state}]")
    if s.current_file:
        lines.append(f"Current file: {s.current_file}")
    if s.previous_action:
        lines.append(f"Previous action: {s.previous_action}")
    if s.previous_command:
        lines.append(f'Previous command: "{s.previous_command}"')
    return "\n".join(lines)


def has_grounding(ctx) -> bool:
    s = ctx.session.state
    return bool(s.active_application or s.current_website or s.current_file or s.current_video.title)


def needs_clarification(ctx, transcript: str) -> str | None:
    """
    If the transcript leans on a reference word ("it", "that", ...) and
    memory has nothing active to ground it, ask instead of guessing.
    """
    words = set(re.findall(r"[a-z']+", transcript.lower()))
    if not (words & _REFERENCE_WORDS):
        return None
    if has_grounding(ctx):
        return None
    return "I don't have anything active in memory to refer to — could you be more specific?"
