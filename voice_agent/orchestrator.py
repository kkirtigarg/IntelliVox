"""Orchestrator: the main control loop.

Responsible for wiring together, in this fixed order, for every user
utterance:

  1. ControlDetector (deterministic) -- pause/resume/cancel/correction always
     checked first, independent of the LLM.
  2. Planner (LLM) -- only reached if no control command fired.
  3. PolicyEngine (deterministic) -- every proposed action, no exceptions.
  4. Confirmation gate -- for any action requiring it.
  5. Actuator -- execute.
  6. Verifier -- check it actually happened.
  7. AuditLog -- record every step above, regardless of outcome.

The orchestrator itself makes no safety judgment calls; it just calls the
right component in the right order and respects their answers.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from .actuators.base import Actuator
from .audit import AuditLog
from .control_detector import detect as detect_control
from .models import (Action, ControlCommand, PolicyContext, Task, TaskState)
from .planner import Planner
from .policy_engine import PolicyEngine
from .session_memory import SessionMemory, resolve_search_result_url
from .state_machine import TaskStateMachine
from .verification import verify
from .voice_pipeline import VoicePipeline

CONFIRM_YES = {"yes", "y", "confirm", "do it", "go ahead", "yep", "sure", "proceed"}
CONFIRM_NO = {"no", "n", "cancel", "don't", "dont", "stop", "nope"}

# Policy action categories don't always map 1:1 onto actuator method names
# (the policy layer distinguishes new-file vs overwrite risk; the actuator
# just has one file_write method). This table bridges that gap explicitly.
ACTION_TO_METHOD = {
    "file_write_new": ("file_write", lambda args: {**args, "overwrite": False}),
    "file_write_overwrite": ("file_write", lambda args: {**args, "overwrite": True}),
    "open_app": ("open_application", lambda args: args),
    "gui_click": ("click", lambda args: args),
    "gui_type": ("type_text", lambda args: args),
    "read_window_titles": ("list_windows", lambda args: {}),
    "read_screen_text": ("read_screen_text", lambda args: {}),
    "screenshot": ("screenshot", lambda args: {}),
    "read_page_text": ("read_page_text", lambda args: {}),
}



class Orchestrator:
    def __init__(self, voice: VoicePipeline, actuator: Actuator,
                 policy_engine: PolicyEngine | None = None,
                 planner: Planner | None = None,
                 audit: AuditLog | None = None,
                 state_dir: Path | None = None,
                 app_allowlist: tuple = (),
                 domain_allowlist: tuple = (),
                 confirm_plans: bool = False,
                 memory: SessionMemory | None = None):
        self.voice = voice
        self.actuator = actuator
        self.policy = policy_engine or PolicyEngine.load()
        self.planner = planner or Planner()
        self.audit = audit or AuditLog()
        self.state_dir = state_dir or Path("state")
        self.app_allowlist = app_allowlist
        self.domain_allowlist = domain_allowlist
        self.deletes_this_task = 0
        # When True, show a plain-English plan and require yes before acting.
        # Stops Whisper/LLM mis-hears from executing the wrong task.
        self.confirm_plans = confirm_plans
        # Persists across completed tasks so follow-ups like
        # "open the first link" work after a Google search.
        self.memory = memory or SessionMemory()

    # ------------------------------------------------------------------
    def handle_utterance(self, transcript: str, task: Task | None = None) -> Task:
        """Process one utterance. If `task` is provided and mid-flight,
        control commands (pause/resume/cancel/correction) apply to it.
        Otherwise a new task is created from the transcript.
        """
        self.audit.log("transcript_received", task.id if task else "new", transcript=transcript)
        self.memory.remember_transcript(transcript)

        control = detect_control(transcript)
        if task is not None and control != ControlCommand.NONE:
            return self._handle_control(control, task, transcript)

        if task is None:
            task = Task(id=str(uuid.uuid4())[:8], original_transcript=transcript)
        sm = TaskStateMachine(task, state_dir=self.state_dir)
        sm.transition(TaskState.PLANNING)
        return self._plan_and_maybe_run(task, sm, transcript)

    def _handle_control(self, control: ControlCommand, task: Task, transcript: str) -> Task:
        self.audit.log("control_command", task.id, command=control.value, transcript=transcript)
        sm = TaskStateMachine(task, state_dir=self.state_dir)

        # Control commands are deterministic, but they only make sense in
        # certain states (e.g. you can't resume a task that isn't paused).
        # Rather than raising on an illegal transition, report the mismatch
        # back to the user -- crashing on an out-of-context "resume" would
        # itself be a control-handling failure.
        if control == ControlCommand.PAUSE:
            if not sm.can_transition(TaskState.PAUSED):
                self.voice.speak("There's nothing currently running to pause.")
                return task
            sm.transition(TaskState.PAUSED)
            self.voice.speak("Paused. Say 'resume' to continue.")
        elif control == ControlCommand.RESUME:
            if not sm.can_transition(TaskState.RUNNING):
                self.voice.speak("Nothing is paused right now.")
                return task
            sm.transition(TaskState.RUNNING)
            self.voice.speak("Resuming.")
            self._run_remaining_steps(task, sm)
        elif control == ControlCommand.CANCEL:
            if not sm.can_transition(TaskState.CANCELLED):
                self.voice.speak("There's nothing active to cancel.")
                return task
            sm.transition(TaskState.CANCELLED)
            self.voice.speak("Cancelled. No further steps will run.")
        elif control == ControlCommand.CORRECTION:
            if not sm.can_transition(TaskState.PLANNING):
                self.voice.speak("There's nothing in progress to correct.")
                return task
            # A correction re-plans the *remaining* steps; already-completed
            # steps are not undone/redone, and the revised plan goes through
            # the policy engine from scratch (no inherited approval).
            sm.transition(TaskState.PLANNING)
            self.voice.speak("Got it, let me revise the plan.")
            return self._plan_and_maybe_run(task, sm, transcript, is_correction=True)
        return task

    # ------------------------------------------------------------------
    def _plan_and_maybe_run(self, task: Task, sm: TaskStateMachine, transcript: str,
                             is_correction: bool = False) -> Task:
        context = f"Task so far: {[s.action.category for s in task.plan.steps]}" if is_correction else ""
        mem_blob = self.memory.context_blob()
        if mem_blob:
            context = (context + "\n" if context else "") + mem_blob
        result = self.planner.plan(
            transcript,
            conversation_context=context,
            memory={
                "last_search_query": self.memory.last_search_query,
                "last_url": self.memory.last_url,
                "last_app": self.memory.last_app,
            },
        )
        self.audit.log("plan_generated", task.id,
                        clarification=result.clarification_question,
                        steps=[s.action.category for s in result.plan.steps] if result.plan else [])

        if result.clarification_question:
            sm.transition(TaskState.WAITING_CLARIFICATION)
            self.voice.speak(result.clarification_question)
            return task

        if is_correction:
            # Replace only the not-yet-completed portion of the plan.
            done = [s for s in task.plan.steps if s.status == "done"]
            task.plan.steps = done + result.plan.steps
        else:
            task.plan = result.plan

        if self.confirm_plans:
            summary = self._describe_plan(task.plan.steps, transcript)
            self.voice.speak(summary)
            self.audit.log("plan_confirmation_prompt", task.id, summary=summary)
            confirm_fn = getattr(self.voice, "confirm", None)
            if callable(confirm_fn):
                reply = confirm_fn(
                    "Type yes / no / or the correct command: "
                ).strip().lower()
            else:
                reply = self.voice.listen().strip().lower()
            self.audit.log("plan_confirmation_response", task.id, reply=reply)
            if reply in CONFIRM_NO or reply in {"retry", "r", "again", "wrong"}:
                sm.transition(TaskState.CANCELLED)
                self.voice.speak("Okay, cancelled. Try the command again.")
                return task
            if reply and reply not in CONFIRM_YES and reply not in {"", "ok", "okay", "run"}:
                # User typed a corrected instruction — re-plan that instead.
                self.voice.speak("Got it — using your correction.")
                return self._plan_and_maybe_run(task, sm, reply, is_correction=True)

        sm.transition(TaskState.RUNNING)
        self._run_remaining_steps(task, sm)
        return task

    @staticmethod
    def _describe_plan(steps, transcript: str) -> str:
        """Plain-English plan so the user can catch ASR / planner mistakes."""
        lines = [f'I heard: "{transcript}".', "I will:"]
        for i, step in enumerate(steps, 1):
            action = step.action
            cat = action.category
            args = action.args
            if cat == "open_app":
                detail = f"open {args.get('app')}"
            elif cat == "browser_navigate":
                detail = f"open {args.get('url')} in {args.get('browser', 'the browser')}"
            elif cat == "open_search_result":
                n = args.get("index", 1)
                q = args.get("query", "the last search")
                detail = f"open the #{n} Google result for {q!r}"
            elif cat == "file_write_new":
                detail = f"create file {args.get('path')}"
            elif cat == "file_write_overwrite":
                detail = f"overwrite file {args.get('path')}"
            elif cat == "file_delete":
                detail = f"delete file {args.get('path')}"
            elif cat == "open_file":
                detail = f"open file {args.get('path')}"
            elif cat == "gui_type":
                detail = f'type "{args.get("text", "")}"'
            elif cat == "reset_environment":
                detail = "reset the desktop environment"
            elif cat == "screenshot":
                detail = "take a screenshot"
            elif cat == "read_window_titles":
                detail = "list open windows"
            else:
                detail = cat.replace("_", " ")
            lines.append(f"  {i}. {detail}")
        lines.append("Say yes to run, no to cancel, or type the correct command.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def _run_remaining_steps(self, task: Task, sm: TaskStateMachine) -> None:
        while task.current_step_index < len(task.plan.steps):
            if task.state != TaskState.RUNNING:
                return  # paused/cancelled mid-loop
            step = task.plan.steps[task.current_step_index]
            action = step.action

            context = PolicyContext(
                app_allowlist=self.app_allowlist,
                domain_allowlist=self.domain_allowlist,
                deletes_this_task=self.deletes_this_task,
            )
            decision = self.policy.evaluate(action, context)
            self.audit.log("policy_decision", task.id, step_id=step.id,
                            category=action.category, args=action.args,
                            decision=decision.to_dict())

            if not decision.allowed:
                step.status = "failed"
                self.voice.speak(f"I can't do that step ({action.category}): {decision.reason}")
                sm.transition(TaskState.FAILED)
                return

            if decision.requires_confirmation:
                if not self._confirm(task, step, action):
                    step.status = "skipped"
                    sm.transition(TaskState.CANCELLED)
                    self.voice.speak("Okay, I won't do that. Stopping here.")
                    return

            self.voice.speak(self._announce(action))
            step.status = "running"
            try:
                exec_result = self._execute(action)
            except Exception as e:
                step.status = "failed"
                self.audit.log("error", task.id, step_id=step.id, error=str(e))
                self.voice.speak(f"Something went wrong on step '{action.category}': {e}")
                sm.transition(TaskState.FAILED)
                return

            self.audit.log("action_executed", task.id, step_id=step.id,
                            category=action.category, result=exec_result)
            self.memory.remember_action(action.category, action.args, exec_result)

            v = verify(action, exec_result, self.actuator)
            self.audit.log("verification_result", task.id, step_id=step.id,
                            verified=v.verified, detail=v.detail)

            if not v.verified:
                step.status = "failed"
                self.voice.speak(
                    f"I tried '{action.category}' but couldn't verify it worked: {v.detail}"
                )
                sm.transition(TaskState.FAILED)
                return

            if action.category == "file_delete":
                self.deletes_this_task += 1

            step.status = "done"
            task.current_step_index += 1
            sm._snapshot()  # persist progress after every completed step

        sm.transition(TaskState.COMPLETED)
        self.voice.speak("Done.")

    @staticmethod
    def _announce(action: Action) -> str:
        cat = action.category
        args = action.args
        if cat == "open_app":
            return f"Opening {args.get('app', 'the app')}…"
        if cat == "browser_navigate":
            return f"Opening {args.get('url', 'the page')} in {args.get('browser', 'the browser')}…"
        if cat == "open_search_result":
            return (
                f"Opening Google result #{args.get('index', 1)} "
                f"for {args.get('query', 'your search')}…"
            )
        if cat == "gui_type":
            return "Typing…"
        if cat == "screenshot":
            return "Taking a screenshot…"
        if cat == "read_window_titles":
            return "Listing open windows…"
        if cat == "file_read":
            return f"Reading {args.get('path', 'the file')}…"
        if cat == "file_write_new":
            return f"Creating {args.get('path', 'the file')}…"
        if cat == "open_file":
            return f"Opening file {args.get('path', '')}…"
        if cat == "file_delete":
            return f"Deleting {args.get('path', 'the file')}…"
        if cat == "reset_environment":
            return "Resetting the participant desktop environment…"
        return f"Running {cat.replace('_', ' ')}…"

    # ------------------------------------------------------------------
    def _confirm(self, task: Task, step, action: Action) -> bool:
        fields = self.policy.fields_to_disclose(action)
        disclosure = ", ".join(f"{f}={action.args.get(f)}" for f in fields) if fields else str(action.args)
        prompt = f"This will {action.category.replace('_', ' ')} ({disclosure}). Proceed?"
        self.voice.speak(prompt)
        self.audit.log("confirmation_prompt", task.id, step_id=step.id, prompt=prompt)

        reply = self.voice.listen().strip().lower()
        self.audit.log("confirmation_response", task.id, step_id=step.id, reply=reply)

        if reply in CONFIRM_YES:
            return True
        if reply in CONFIRM_NO:
            return False
        # Ambiguous or unrecognized reply: treat as "no" (fail closed).
        self.voice.speak("I didn't catch a clear yes or no, so I'll treat that as no.")
        return False

    # ------------------------------------------------------------------
    def _execute(self, action: Action) -> dict:
        if action.category == "reset_environment":
            from .eval_env import reset_environment
            result = reset_environment(action.args.get("task_id"))
            return {"success": True, **result}

        if action.category == "open_search_result":
            query = action.args.get("query") or self.memory.last_search_query or ""
            index = int(action.args.get("index") or 1)
            browser = action.args.get("browser") or "firefox"
            url = resolve_search_result_url(query, index)
            result = self.actuator.browser_navigate(url, browser=browser)
            if isinstance(result, dict):
                result = {**result, "url": result.get("url") or url, "query": query, "index": index}
                return result
            return {"success": True, "url": url, "query": query, "index": index}

        method_name, args = action.category, dict(action.args)
        if action.category in ACTION_TO_METHOD:
            method_name, transform = ACTION_TO_METHOD[action.category]
            args = transform(action.args)
        method = getattr(self.actuator, method_name, None)
        if method is None:
            raise RuntimeError(f"Actuator has no implementation for '{method_name}'")
        result = method(**args) if args else method()
        # Normalize non-dict actuator returns so verification/audit stay consistent.
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"success": True, "items": result, "detail": result}
        if isinstance(result, str):
            return {"success": True, "text": result, "detail": result}
        return {"success": True, "detail": result}
