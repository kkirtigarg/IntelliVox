"""
agent/tools/computer.py
General computer-use tool — delegates to the vision-based agent loop.
"""
from agent.computer_use import run_computer_task


def computer_use(goal: str) -> dict:
    """
    Perform an arbitrary desktop task like a human: see screen, click, type, navigate.
    Use for: play a song, click first link, fill forms, interact with any app/web page.
    """
    result = run_computer_task(goal)
    return {
        "success": result.get("success", False),
        "message": result.get("message", ""),
        "steps_taken": result.get("steps", 0),
        "log": result.get("log", []),
    }
