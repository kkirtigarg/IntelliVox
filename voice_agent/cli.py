"""CLI entrypoint.

No API key required. Default planner is offline rules (+ optional local Ollama).

Demo (mock actuator + text voice — runs anywhere):

    python -m voice_agent.cli --demo

Participant eval desktop (apps + sample files + reset):

    python -m voice_agent.cli --reset-env
    python -m voice_agent.cli --real --text --task task_open_invoice

Real usage with VOICE (microphone) on Linux/Windows:

    python -m voice_agent.cli --real --voice
    ./run_linux.sh

Force typed input instead:

    python -m voice_agent.cli --real --text
    ./run_linux.sh --text
"""
from __future__ import annotations

import argparse
import json
import sys

from .actuators.desktop import build_desktop_actuator, desktop_platform_label
from .actuators.mock import MockActuator
from .audit import AuditLog
from .eval_env import (
    WORKSPACE_DIR,
    eval_app_allowlist,
    get_task,
    load_tasks,
    reset_environment,
    workspace_path,
)
from .models import Task, TaskState
from .orchestrator import Orchestrator
from .planner import Planner
from .policy_engine import PolicyEngine
from .voice_pipeline import TextModeVoicePipeline, build_voice_pipeline


if sys.platform.startswith("linux"):
    DEFAULT_APPS = eval_app_allowlist() + (
        "vscode", "code", "terminal", "cmd", "gimp", "paint",
        "spotify", "slack", "calculator",
    )
else:
    DEFAULT_APPS = (
        "notepad", "excel", "word", "chrome", "edge", "firefox", "outlook",
        "calculator", "calc", "explorer", "paint", "cmd", "powershell",
        "spotify", "teams", "slack", "vscode", "code",
        "browser", "files", "pdf", "spreadsheet", "writer", "impress",
    )

DEFAULT_DOMAINS = (
    "wikipedia.org", "www.wikipedia.org", "en.wikipedia.org",
    "google.com", "www.google.com",
    "company-intranet.local", "github.com", "www.github.com",
)


def build_real_actuator(with_browser: bool):
    desktop = build_desktop_actuator()
    if not with_browser:
        return desktop
    try:
        from .actuators.browser import BrowserActuator
        from .actuators.composite import CompositeActuator
        browser = BrowserActuator(headless=False)
        return CompositeActuator(desktop=desktop, browser=browser)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] BrowserActuator unavailable ({exc}); using desktop GUI only.")
        return desktop


def run(
    real: bool = False,
    prefer_audio: bool = True,
    planner_backend: str = "auto",
    with_browser: bool = False,
    task_id: str | None = None,
    confirm_plans: bool = True,
):
    workspace_path()  # ensure participant workspace exists

    if real:
        if sys.platform not in ("win32",) and not sys.platform.startswith("linux"):
            print(
                f"[warn] --real is supported on Windows and Linux; "
                f"current platform is {sys.platform!r}. GUI calls may fail."
            )
        actuator = build_real_actuator(with_browser=with_browser)
        voice = build_voice_pipeline(prefer_audio=prefer_audio)
    else:
        actuator = MockActuator()
        voice = TextModeVoicePipeline()

    orch = Orchestrator(
        voice=voice,
        actuator=actuator,
        policy_engine=PolicyEngine.load(),
        planner=Planner(backend=planner_backend),
        audit=AuditLog(),
        app_allowlist=DEFAULT_APPS,
        domain_allowlist=DEFAULT_DOMAINS,
        confirm_plans=confirm_plans,
    )

    if real:
        mode = f"REAL ({desktop_platform_label()})"
    else:
        mode = "DEMO (mock)"
    voice_mode = type(voice).__name__
    print(f"Voice-controlled computer-use agent — {mode}")
    print(f"Planner backend: {planner_backend} | Voice: {voice_mode}")
    print(f"Participant workspace: {WORKSPACE_DIR}")
    print("No cloud API key required (rules + optional local Ollama).")
    if isinstance(voice, TextModeVoicePipeline):
        print("Input: TYPE at the prompt.")
    else:
        print("Input: PUSH-TO-TALK — ENTER to start, speak, ENTER to stop, then confirm.")

    if task_id:
        task_def = get_task(task_id)
        if task_def is None:
            print(f"[error] Unknown task {task_id!r}. Known:")
            for t in load_tasks():
                print(f"  - {t.id}: {t.title}")
            sys.exit(2)
        reset_environment(task_id)
        print(f"\n=== Task: {task_def.title} ({task_def.id}) ===")
        print(f"Instruction: {task_def.instruction}")
        voice.speak(f"Your task is: {task_def.instruction}")

    print(
        "\nPreconfigured apps: browser, files, pdf viewer, text editor,\n"
        "  spreadsheet (Calc), document editor (Writer), presentation (Impress).\n"
        "Sample files live under environment/workspace/Documents/.\n"
        "Anti-hallucination: offline rules planner + you confirm the plan before it runs.\n"
        "If [heard] is wrong, TYPE the real command. Then type yes/no for the plan.\n"
        "Examples:\n"
        "  open pdf viewer\n"
        "  open invoice.pdf\n"
        "  open firefox and search for cats\n"
        "  open the first link          (after a search — uses session memory)\n"
        "  open vscode and create a file with name app.py\n"
        "Say/type 'quit' to exit.\n"
    )

    task: Task | None = None
    try:
        while True:
            transcript = voice.listen()
            if not transcript:
                continue
            if transcript.lower() in ("quit", "exit"):
                break
            if task is not None and task.state in (
                TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED,
            ):
                task = None
            task = orch.handle_utterance(transcript, task=task)
    finally:
        closer = getattr(actuator, "close", None)
        if callable(closer):
            closer()


def main():
    parser = argparse.ArgumentParser(
        description="Voice-controlled computer-use agent (works without API keys)",
    )
    parser.add_argument("--real", action="store_true",
                        help="Use real desktop GUI on Windows/Linux (+ mic by default)")
    parser.add_argument("--demo", action="store_true",
                        help="Use mock actuator + text mode (default)")
    parser.add_argument("--text", action="store_true",
                        help="Force typed input/output instead of microphone")
    parser.add_argument("--voice", action="store_true",
                        help="Force microphone + speech (default with --real)")
    parser.add_argument(
        "--planner",
        choices=("auto", "rules", "ollama", "anthropic"),
        default="rules",
        help="Planning backend (default: rules = offline, most reliable for open/search)",
    )
    parser.add_argument(
        "--with-browser",
        action="store_true",
        help="In --real mode, also attach Playwright BrowserActuator",
    )
    parser.add_argument(
        "--reset-env",
        action="store_true",
        help="Reset participant workspace from golden sample files and exit",
    )
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="List predefined evaluation tasks and exit",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Reset env and load a predefined task (see --list-tasks)",
    )
    parser.add_argument(
        "--no-confirm-plan",
        action="store_true",
        help="Skip the 'I heard / I will' confirmation (not recommended)",
    )
    args = parser.parse_args()

    if args.list_tasks:
        for t in load_tasks():
            print(f"{t.id}\n  {t.title}\n  {t.instruction}\n")
        return

    if args.reset_env:
        info = reset_environment(args.task)
        print(json.dumps(info, indent=2))
        return

    prefer_audio = True
    if args.text:
        prefer_audio = False
    elif args.voice or args.real:
        prefer_audio = True
    run(
        real=args.real,
        prefer_audio=prefer_audio,
        planner_backend=args.planner,
        with_browser=args.with_browser,
        task_id=args.task,
        confirm_plans=not args.no_confirm_plan,
    )


if __name__ == "__main__":
    main()
