"""
agent/orchestrator.py
IntelliVox main server — combines Whisper ASR + Agent execution over WebSocket.

Message protocol (server → client):
  { "type": "transcribed",   "text": "...", "language": "en" }
  { "type": "planning",      "text": "Figuring out your task…" }
  { "type": "plan",          "intent": "...", "explanation": "...", "steps": [...] }
  { "type": "safety_block",  "text": "I can't do that: ..." }
  { "type": "confirm",       "text": "About to X — OK?", "tool": "...", "step_index": 0 }
  { "type": "executing",     "step_index": 0, "tool": "...", "text": "Opening Chrome..." }
  { "type": "step_done",     "step_index": 0, "verified": true, "text": "Chrome opened ✓" }
  { "type": "step_failed",   "step_index": 0, "text": "Failed: ..." }
  { "type": "clarify",       "text": "Could you rephrase...?" }
  { "type": "done",          "text": "All done!" }
  { "type": "error",         "text": "..." }
  { "type": "popup",         "kind": "cancelled"|"changed"|"unsafe"|"uncertain"|"impossible",
                               "title": "...", "text": "..." }
                               ← cancel/change mid-task, OR task cannot complete safely/confidently
  { "type": "info",          "text": "..." }

Message protocol (client → server):
  Binary: raw audio blob (webm/opus) → triggers transcription + agent
  Text:   "confirm:yes"  or  "confirm:no"  → for confirmation prompts
  Text:   "cancel"  → abort current task (popup response)
  Text:   "pause"   → pause after current step
  Text:   "resume"  → resume paused task
"""

import asyncio
import json
import logging
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel

from agent import safety, tts
from agent.planner import plan, resolve_step_args
from agent.tools import run_tool
from agent.verifier import verify_step
from agent.audit import AuditSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("intellivox")

WHISPER_MODEL_ID = "Systran/faster-whisper-medium"
SAMPLE_RATE      = 16_000

whisper_model: WhisperModel | None = None

app = FastAPI(title="IntelliVox Agent Server")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global whisper_model
    log.info("Loading Whisper model: %s …", WHISPER_MODEL_ID)
    loop = asyncio.get_event_loop()
    whisper_model = await loop.run_in_executor(None, _load_whisper)
    log.info("✓ Whisper ready")
    log.info("✓ IntelliVox Agent Server on ws://localhost:8765/ws")


def _load_whisper() -> WhisperModel:
    return WhisperModel(WHISPER_MODEL_ID, device="auto", compute_type="int8")


# ── Audio conversion ───────────────────────────────────────────────────────────

def _audio_suffix(data: bytes) -> str:
    """Pick a file extension so ffmpeg can parse the container."""
    if len(data) >= 4 and data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return ".mp4"
    if len(data) >= 4 and data[:4] == b"OggS":
        return ".ogg"
    if len(data) >= 4 and data[:4] == b"RIFF":
        return ".wav"
    return ".webm"


def webm_to_pcm(data: bytes) -> np.ndarray:
    suffix = _audio_suffix(data)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    cmd = ["ffmpeg", "-y", "-i", tmp_path, "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "f32le", "-"]
    result = subprocess.run(cmd, capture_output=True)
    Path(tmp_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg ({suffix}): {result.stderr.decode()}")
    return np.frombuffer(result.stdout, dtype=np.float32)


def transcribe(audio: np.ndarray) -> dict:
    # Force English — auto-detect often mislabels Indian-accent English as Hindi
    segments, info = whisper_model.transcribe(audio, beam_size=5, language="en")
    text = " ".join(s.text.strip() for s in segments).strip()
    return {"text": text, "language": info.language, "confidence": round(info.language_probability, 2)}


# ── Agent execution loop ───────────────────────────────────────────────────────

# ── Mid-task interrupt helpers ────────────────────────────────────────────────

_CANCEL_PHRASES = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"cancel(?:\s+(?:that|it|this|the\s+task|task))?|"
    r"stop(?:\s+(?:that|it|this|the\s+task|task))?|"
    r"abort|"
    r"never\s*mind|"
    r"forget\s+(?:it|that)|"
    r"don'?t\s+(?:do\s+)?(?:that|it)|"
    r"quit|"
    r"enough"
    r")\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _is_cancel_phrase(text: str) -> bool:
    """True when the spoken utterance is only a cancel/stop request."""
    if not text or not text.strip():
        return False
    return bool(_CANCEL_PHRASES.match(text.strip()))


class AgentSession:
    """Manages one active WebSocket connection + task execution."""

    def __init__(self, ws: WebSocket):
        self.ws         = ws
        self.session_id = str(uuid.uuid4())[:8]
        self.audit      = AuditSession(self.session_id)
        self._paused    = False
        self._cancelled = False
        self._confirm_event  = asyncio.Event()
        self._confirm_result = None
        self.history_turns   = []
        # Mid-task interrupt state
        self._busy = False
        self._popup_sent = False
        self._interrupt_kind: str | None = None  # cancelled | changed
        self._active_task: asyncio.Task | None = None
        self._run_id = 0
        self._completed_before_interrupt = 0
        self._total_steps_before_interrupt = 0

    async def send(self, msg: dict):
        await self.ws.send_text(json.dumps(msg))

    async def send_popup(self, kind: str, title: str, text: str, *, speak: bool = True):
        """Show a client popup for mid-task cancel / instruction change."""
        self._popup_sent = True
        self._interrupt_kind = kind
        payload = {
            "type":  "popup",
            "kind":  kind,
            "title": title,
            "text":  text,
        }
        try:
            await self.send(payload)
        except Exception:
            log.exception("[%s] failed to send popup [%s]", self.session_id, kind)
        if speak:
            try:
                tts.speak(text)
            except Exception:
                log.exception("[%s] TTS failed for popup", self.session_id)
        log.info("[%s] popup [%s]: %s", self.session_id, kind, text)

    def _release_waiters(self):
        """Unblock confirmation waits so the task loop can exit promptly."""
        self._confirm_result = False
        self._confirm_event.set()

    async def abort_current(self, kind: str, message: str | None = None) -> bool:
        """
        Cancel the in-flight task and notify the UI with a popup.
        Returns True if a task was actually busy.
        """
        if not self._busy and not self._cancelled:
            # Still send a cancel popup if the client pressed Cancel with no task
            if kind == "cancelled":
                await self.send_popup(
                    "cancelled",
                    "Nothing to cancel",
                    "There is no task running right now.",
                )
            return False

        self._cancelled = True
        self._paused = False
        self._release_waiters()

        done = self._completed_before_interrupt
        total = self._total_steps_before_interrupt
        if kind == "cancelled":
            title = "Instruction cancelled"
            text = message or (
                f"Okay — I cancelled the current task"
                + (f" after {done} of {total} steps." if total else ".")
            )
        else:
            title = "Instruction updated"
            text = message or "Got it — switching to your new instruction."

        # Always push a popup for cancel/change so the UI never misses it
        await self.send_popup(kind, title, text)
        self.audit.log_outcome(kind, text)
        return True

    async def handle_control(self, text: str):
        """Handle control messages from the client."""
        text = text.strip().lower()
        if text == "cancel":
            log.info("[%s] Cancel requested by user", self.session_id)
            await self.abort_current("cancelled")
        elif text == "pause":
            self._paused = True
            await self.send({"type": "info", "text": "Task paused. Say 'resume' to continue."})
            tts.speak("Task paused.")
        elif text == "resume":
            self._paused = False
            await self.send({"type": "info", "text": "Resuming…"})
            tts.speak("Resuming.")
        elif text.startswith("confirm:"):
            answer = text.split(":", 1)[1].strip()
            self._confirm_result = (answer == "yes")
            self._confirm_event.set()

    async def wait_for_confirmation(self) -> bool:
        """Wait until the user sends confirm:yes or confirm:no (or cancels)."""
        self._confirm_event.clear()
        self._confirm_result = None
        try:
            await asyncio.wait_for(self._confirm_event.wait(), timeout=30.0)
            if self._cancelled:
                return False
            return bool(self._confirm_result)
        except asyncio.TimeoutError:
            return False  # treat timeout as "no"

    async def handle_new_transcript(self, result: dict):
        """
        Start a task from a transcript. If one is already running, treat the
        new speech as a mid-task cancel or instruction change and popup.
        """
        text = (result.get("text") or "").strip()

        if self._busy:
            if _is_cancel_phrase(text):
                await self.abort_current(
                    "cancelled",
                    "Okay — I've cancelled the current instruction.",
                )
                return

            # Change of instruction mid-way
            await self.abort_current(
                "changed",
                f"Got it. Cancelling the previous task and starting: {text}",
            )

        # Invalidate any still-finishing prior run, then start fresh
        self._run_id += 1
        run_id = self._run_id
        task = asyncio.create_task(self.run_task(result, run_id=run_id))
        self._active_task = task

        def _clear(t: asyncio.Task, rid=run_id):
            if self._active_task is t:
                self._active_task = None

        task.add_done_callback(_clear)

    async def run_task(self, transcript_result: dict, run_id: int | None = None):
        """Full pipeline: transcript → plan → safety → execute → verify."""
        if run_id is None:
            self._run_id += 1
            run_id = self._run_id

        # Fresh task — clear leftover cancel/pause from a previous run
        self._cancelled = False
        self._paused = False
        self._popup_sent = False
        self._interrupt_kind = None
        self._confirm_event.clear()
        self._confirm_result = None
        self._busy = True
        self._completed_before_interrupt = 0
        self._total_steps_before_interrupt = 0

        try:
            await self._run_task_body(transcript_result, run_id=run_id)
        finally:
            # Only the latest run may clear the busy flag
            if run_id == self._run_id:
                self._busy = False

    async def _run_task_body(self, transcript_result: dict, run_id: int = 0):
        def _stale() -> bool:
            return run_id != self._run_id or self._cancelled
        text     = transcript_result["text"]
        language = transcript_result["language"]

        if not text:
            await self.send({"type": "error", "text": "I didn't catch that. Please try again."})
            return

        self.audit.log_transcript(text, language)
        log.info("[%s] Transcript [%s]: %s", self.session_id, language, text)

        # ── 0. Can we complete this safely & confidently? ────────────────────
        asr_conf = transcript_result.get("confidence")
        try:
            asr_conf_f = float(asr_conf) if asr_conf is not None else None
        except (TypeError, ValueError):
            asr_conf_f = None

        request_assessment = safety.assess_request(text, asr_confidence=asr_conf_f)
        log.info(
            "[%s] Request assessment: %s (%s) — %s",
            self.session_id,
            request_assessment.kind,
            request_assessment.confidence,
            request_assessment.reason,
        )
        if not request_assessment.can_proceed:
            await self.send_popup(
                request_assessment.kind,
                request_assessment.title,
                request_assessment.message,
            )
            self.audit.log_outcome(request_assessment.kind, request_assessment.message)
            return

        # ── 1. Plan ──────────────────────────────────────────────────────────
        await self.send({"type": "planning", "text": "Figuring out your task…"})
        tts.speak("Let me figure that out.")

        loop = asyncio.get_event_loop()
        action_plan = await loop.run_in_executor(None, plan, text)
        self.audit.log_plan(action_plan)

        if _stale():
            if run_id == self._run_id and not self._popup_sent:
                await self.send_popup(
                    self._interrupt_kind or "cancelled",
                    "Instruction cancelled",
                    "Okay — I stopped before starting.",
                )
            return

        plan_assessment = safety.assess_plan(action_plan, transcript=text)
        log.info(
            "[%s] Plan assessment: %s — %s",
            self.session_id,
            plan_assessment.kind,
            plan_assessment.reason,
        )
        if not plan_assessment.can_proceed:
            # Prefer clarify-style UX for soft uncertainty; popup for hard refusals
            if plan_assessment.kind == "uncertain" and action_plan.get("clarification_needed"):
                await self.send({"type": "clarify", "text": plan_assessment.message})
                tts.speak(plan_assessment.message)
            else:
                await self.send_popup(
                    plan_assessment.kind,
                    plan_assessment.title,
                    plan_assessment.message,
                )
            self.audit.log_outcome(plan_assessment.kind, plan_assessment.message)
            return

        await self.send({
            "type":        "plan",
            "intent":      action_plan.get("intent", ""),
            "explanation": action_plan.get("explanation", ""),
            "steps":       action_plan.get("steps", []),
            "confidence":  plan_assessment.confidence,
        })

        steps = action_plan.get("steps", [])
        self._total_steps_before_interrupt = len(steps)
        if not steps:
            await self.send_popup(
                "uncertain",
                "Can't complete that",
                "I couldn't figure out safe steps for that request.",
            )
            return

        # ── 2. Execute steps ─────────────────────────────────────────────────
        completed    = 0
        consecutive_failures = 0
        step_results = []   # stores each step's raw result for chaining
        for i, step in enumerate(steps):
            if _stale():
                if run_id == self._run_id and not self._popup_sent:
                    await self.send_popup(
                        self._interrupt_kind or "cancelled",
                        "Instruction cancelled" if self._interrupt_kind != "changed" else "Instruction updated",
                        (
                            f"Stopped after {completed} of {len(steps)} steps."
                            if self._interrupt_kind != "changed"
                            else "Previous task stopped — starting your new instruction."
                        ),
                    )
                self.audit.log_outcome(self._interrupt_kind or "cancelled", "Interrupted mid-task")
                return

            # Wait while paused
            while self._paused and not _stale():
                await asyncio.sleep(0.3)
            if _stale():
                if run_id == self._run_id and not self._popup_sent:
                    await self.send_popup(
                        "cancelled",
                        "Instruction cancelled",
                        f"Okay — cancelled while paused ({completed}/{len(steps)} done).",
                    )
                return

            tool = step.get("tool", "")
            args = step.get("args", {})

            # ── Resolve placeholders from previous step results (chaining) ────
            args = resolve_step_args(args, step_results)
            # Drop internal orchestration markers before calling the tool
            args.pop("_src_filled", None)

            # ── Safety check ─────────────────────────────────────────────────
            safety_result = safety.check(tool, args, source="voice")
            self.audit.log_safety(tool, args, safety_result.decision, safety_result.reason)
            log.info("[%s] Safety %s → %s", self.session_id, tool, safety_result.decision)

            if safety_result.decision == safety.Decision.BLOCK:
                msg = f"I can't do that: {safety_result.reason}"
                await self.send_popup("unsafe", "Can't do that safely", msg)
                self.audit.log_outcome("blocked", msg)
                return

            if safety_result.decision == safety.Decision.CONFIRM:
                confirm_msg = f"Step {i+1}: {safety_result.reason} Shall I proceed?"
                await self.send({
                    "type":       "confirm",
                    "text":       confirm_msg,
                    "tool":       tool,
                    "step_index": i,
                })
                tts.speak(confirm_msg)
                self.audit.log_confirmation(tool, "waiting")

                approved = await self.wait_for_confirmation()
                self.audit.log_confirmation(tool, "yes" if approved else "no")

                if _stale():
                    if run_id == self._run_id and not self._popup_sent:
                        await self.send_popup(
                            self._interrupt_kind or "cancelled",
                            "Instruction cancelled",
                            "Okay — cancelled before that step.",
                        )
                    return

                if not approved:
                    await self.send({"type": "info", "text": "Skipped that step."})
                    tts.speak("Okay, skipping.")
                    continue

            # ── Execute ───────────────────────────────────────────────────────
            step_desc = _describe_step(tool, args)
            await self.send({"type": "executing", "step_index": i, "tool": tool, "text": step_desc})
            log.info("[%s] → %s(%s)", self.session_id, tool, args)

            result = await loop.run_in_executor(None, run_tool, tool, args)

            if _stale():
                if run_id == self._run_id and not self._popup_sent:
                    await self.send_popup(
                        self._interrupt_kind or "cancelled",
                        "Instruction cancelled" if self._interrupt_kind != "changed" else "Instruction updated",
                        f"Stopped during step {i + 1} of {len(steps)}.",
                    )
                return

            # Store result for chaining (next steps can reference it)
            step_results.append(result)

            # ── Verify ────────────────────────────────────────────────────────
            verification = await loop.run_in_executor(None, verify_step, tool, args, result)
            verified = verification.get("verified", False)
            self.audit.log_action(tool, args, result, verified)

            if result.get("success") and verified:
                done_msg = verification.get("message", f"{tool} done")

                # find_file: inject path into next pdf/file/compare step
                if tool == "find_file" and result.get("path"):
                    found_path = result["path"]
                    import os as _os
                    done_msg = f"Found: {_os.path.basename(found_path)}"
                    log.info("[%s] find_file -> %s", self.session_id, found_path)
                    if i + 1 < len(steps):
                        ns = steps[i + 1]
                        next_tool = ns.get("tool")
                        if next_tool == "summarize_codebase":
                            ns["args"]["directory"] = found_path
                            ns["args"].pop("path", None)
                        elif next_tool in ("open_file", "read_pdf", "read_file"):
                            ns["args"]["path"] = found_path
                        elif next_tool == "compare_pdf_with_dummy":
                            ns["args"]["pdf_path"] = found_path
                        elif next_tool == "extract_pdf_to_spreadsheet":
                            ns["args"]["pdf_path"] = found_path
                            ns["args"].pop("path", None)
                    # Fill path_a then path_b on the upcoming compare_open_files step
                    for j in range(i + 1, len(steps)):
                        if steps[j].get("tool") == "compare_open_files":
                            args_j = steps[j].setdefault("args", {})
                            if not args_j.get("path_a"):
                                args_j["path_a"] = found_path
                            elif not args_j.get("path_b") and args_j.get("path_a") != found_path:
                                args_j["path_b"] = found_path
                            break
                        if steps[j].get("tool") == "update_presentation_from_document":
                            args_j = steps[j].setdefault("args", {})
                            src = args_j.get("source_path") or ""
                            ppt = args_j.get("presentation_path") or ""
                            # Treat unresolved placeholders as empty for injection
                            src_empty = (not src) or ("{{" in str(src))
                            ppt_empty = (not ppt) or ("{{" in str(ppt))
                            if src_empty and not args_j.get("_src_filled"):
                                args_j["source_path"] = found_path
                                args_j["_src_filled"] = True
                            elif ppt_empty and found_path != args_j.get("source_path"):
                                args_j["presentation_path"] = found_path
                            # Also append extra source docs when asked for multiple
                            elif (
                                found_path != args_j.get("source_path")
                                and found_path != args_j.get("presentation_path")
                                and str(found_path).lower().endswith((".pdf", ".txt", ".md", ".docx"))
                            ):
                                extra = args_j.get("source_paths") or []
                                if isinstance(extra, str):
                                    extra = [p.strip() for p in extra.replace(";", ",").replace("\n", ",").split(",") if p.strip()]
                                if found_path not in extra:
                                    extra = list(extra) + [found_path]
                                    args_j["source_paths"] = extra
                            break
                        if steps[j].get("tool") == "create_presentation_from_document":
                            args_j = steps[j].setdefault("args", {})
                            src = args_j.get("source_path") or ""
                            if (not src) or ("{{" in str(src)):
                                args_j["source_path"] = found_path
                            break
                        if steps[j].get("tool") == "organize_files":
                            args_j = steps[j].setdefault("args", {})
                            directory = args_j.get("directory") or ""
                            if (not directory) or ("{{" in str(directory)):
                                args_j["directory"] = found_path
                            break

                # read_pdf: inject text into next summarize/answer step
                elif tool == "read_pdf" and result.get("text"):
                    chars = len(result["text"])
                    done_msg = f"Read {result.get('page_count','?')} pages ({chars:,} chars)"
                    if i + 1 < len(steps):
                        ns = steps[i + 1]
                        if ns.get("tool") in ("summarize", "answer_question"):
                            ns["args"]["text"] = result["text"]

                # summarize: inject into next compare/write; show in UI
                elif tool == "summarize" and result.get("summary"):
                    done_msg = "Summary complete"
                    await self.send({"type": "rich_result", "label": "Summary", "text": result["summary"]})
                    short = result["summary"][:220].replace("\n", " ").strip()
                    tts.speak(short)
                    if i + 1 < len(steps):
                        ns = steps[i + 1]
                        if ns.get("tool") == "compare_documents":
                            # Prefer filling the first empty text slot
                            if not ns["args"].get("text_a"):
                                ns["args"]["text_a"] = result["summary"]
                                ns["args"].setdefault("label_a", "PDF Summary")
                            elif not ns["args"].get("text_b"):
                                ns["args"]["text_b"] = result["summary"]
                                ns["args"].setdefault("label_b", "PDF Summary")
                        elif ns.get("tool") == "write_file" and not ns["args"].get("content"):
                            ns["args"]["content"] = result["summary"]

                # summarize_codebase: display full summary in UI + speak excerpt
                elif tool == "summarize_codebase" and result.get("summary"):
                    done_msg = f"Summarized {result.get('files_read', '?')} files"
                    await self.send({"type": "rich_result", "label": "Summary", "text": result["summary"]})
                    short = result["summary"][:220].replace("\n", " ").strip()
                    tts.speak(short)

                # create_dummy_file: inject content into next compare step
                elif tool == "create_dummy_file" and result.get("content"):
                    done_msg = f"Dummy file ready: {result.get('path', '')}"
                    if i + 1 < len(steps):
                        ns = steps[i + 1]
                        if ns.get("tool") == "compare_documents":
                            if not ns["args"].get("text_b"):
                                ns["args"]["text_b"] = result["content"]
                                ns["args"].setdefault("label_b", "Dummy Document")
                            elif not ns["args"].get("text_a"):
                                ns["args"]["text_a"] = result["content"]
                                ns["args"].setdefault("label_a", "Dummy Document")

                # compare_*: show comparison summary in UI + speak excerpt
                elif tool in (
                    "compare_documents",
                    "compare_pdf_with_dummy",
                    "compare_open_files",
                ) and result.get("summary"):
                    done_msg = "Comparison summary complete"
                    if tool == "compare_pdf_with_dummy":
                        parts = []
                        if result.get("pdf_summary_path"):
                            parts.append(f"PDF summary → {result['pdf_summary_path']}")
                        if result.get("dummy_path"):
                            parts.append(f"Dummy → {result['dummy_path']}")
                        if parts:
                            done_msg = "Compared: " + " | ".join(parts)
                    elif tool == "compare_open_files":
                        done_msg = (
                            f"Compared open files: "
                            f"{result.get('label_a', 'A')} vs {result.get('label_b', 'B')}"
                        )
                    await self.send({"type": "rich_result", "label": "Summary", "text": result["summary"]})
                    short = result["summary"][:220].replace("\n", " ").strip()
                    tts.speak(short)

                # answer_question: display answer in UI + speak excerpt
                elif tool == "answer_question" and result.get("answer"):
                    done_msg = "Answer ready"
                    await self.send({"type": "rich_result", "label": "Answer", "text": result["answer"]})
                    short = result["answer"][:220].replace("\n", " ").strip()
                    tts.speak(short)

                # extract_pdf_to_spreadsheet / write_spreadsheet
                elif tool in ("extract_pdf_to_spreadsheet", "write_spreadsheet") and result.get("path"):
                    rows = result.get("row_count", "?")
                    done_msg = f"Spreadsheet ready ({rows} rows) → {result['path']}"
                    headers = result.get("headers") or []
                    preview_rows = result.get("rows") or []
                    preview_lines = []
                    if headers:
                        preview_lines.append(" | ".join(str(h) for h in headers))
                    for r in preview_rows[:8]:
                        if isinstance(r, list):
                            preview_lines.append(" | ".join(str(c) for c in r))
                        else:
                            preview_lines.append(str(r))
                    preview = "\n".join(preview_lines) if preview_lines else result.get("message", "")
                    await self.send({
                        "type": "rich_result",
                        "label": "Spreadsheet",
                        "text": f"{result.get('message', done_msg)}\n\n{preview}".strip(),
                    })
                    tts.speak(f"Saved {rows} rows to the spreadsheet.")

                # presentation update / create
                elif tool in (
                    "update_presentation_from_document",
                    "create_presentation_from_document",
                ) and result.get("path"):
                    count = result.get("slide_count", "?")
                    done_msg = f"Presentation ready ({count} slides) → {result['path']}"
                    slides = result.get("slides") or []
                    preview_lines = []
                    for idx, s in enumerate(slides[:6], 1):
                        title = s.get("title") if isinstance(s, dict) else str(s)
                        preview_lines.append(f"{idx}. {title}")
                        if isinstance(s, dict):
                            for b in (s.get("bullets") or [])[:3]:
                                preview_lines.append(f"   • {b}")
                    preview = "\n".join(preview_lines) if preview_lines else result.get("message", "")
                    await self.send({
                        "type": "rich_result",
                        "label": "Presentation",
                        "text": f"{result.get('message', done_msg)}\n\n{preview}".strip(),
                    })
                    tts.speak(f"Saved a presentation with {count} slides.")

                # organize_files: show what moved where
                elif tool == "organize_files":
                    moved = result.get("moved", 0)
                    done_msg = result.get("message") or f"Organized {moved} files"
                    moves = result.get("moves") or []
                    preview_lines = []
                    for m in moves[:12]:
                        if isinstance(m, dict):
                            name = Path(m.get("src") or m.get("dest") or "").name
                            folder = Path(m.get("folder") or m.get("dest_folder") or m.get("dest") or "").name
                            preview_lines.append(f"• {name} → {folder}")
                    preview = "\n".join(preview_lines)
                    await self.send({
                        "type": "rich_result",
                        "label": "Organized files",
                        "text": f"{done_msg}\n\n{preview}".strip(),
                    })
                    tts.speak(f"Organized {moved} files.")

                await self.send({"type": "step_done", "step_index": i, "verified": True, "text": done_msg})
                log.info("[%s] step %d done: %s", self.session_id, i, done_msg)
                completed += 1
                consecutive_failures = 0
                self._completed_before_interrupt = completed
            else:
                consecutive_failures += 1
                err_msg = result.get("message") or verification.get("message", "Step failed")
                await self.send({"type": "step_failed", "step_index": i, "text": err_msg})
                log.warning("[%s] step %d failed: %s", self.session_id, i, err_msg)
                self.audit.log_error(err_msg)

                failure_assessment = safety.assess_step_failure(
                    tool,
                    result if isinstance(result, dict) else {"message": err_msg},
                    step_index=i,
                    total_steps=len(steps),
                    consecutive_failures=consecutive_failures,
                )
                if failure_assessment and not failure_assessment.can_proceed:
                    if run_id == self._run_id and not self._popup_sent:
                        await self.send_popup(
                            failure_assessment.kind,
                            failure_assessment.title,
                            failure_assessment.message,
                        )
                    self.audit.log_outcome(failure_assessment.kind, failure_assessment.message)
                    return

        # Done
        if _stale():
            return
        # Partial success without a hard abort — still warn if we missed steps
        if completed < len(steps):
            await self.send_popup(
                "uncertain",
                "Couldn't finish confidently",
                f"I only completed {completed} of {len(steps)} steps, so I'm not confident the task finished.",
            )
            self.audit.log_outcome("partial", f"{completed}/{len(steps)}")
            return
        summary_msg = f"Done! Completed {completed} of {len(steps)} steps."
        await self.send({"type": "done", "text": summary_msg})
        if completed == len(steps):
            tts.speak("All done!")
        self.audit.log_outcome("success" if completed == len(steps) else "partial", summary_msg)
        log.info("[%s] Task complete: %s", self.session_id, summary_msg)

def _describe_step(tool: str, args: dict) -> str:
    descs = {
        "open_browser":   lambda a: f"Opening {a.get('browser', 'browser').title()}…",
        "navigate_url":   lambda a: f"Navigating to {a.get('url', '')}…",
        "google_search":  lambda a: f"Searching Google for '{a.get('query', '')}'…",
        "youtube_search": lambda a: f"Searching YouTube for '{a.get('query', '')}'…",
        "open_app":       lambda a: f"Opening {a.get('name', 'app')}…",
        "close_app":      lambda a: f"Closing {a.get('name', 'app')}…",
        "type_text":      lambda a: f"Typing: {a.get('text', '')}…",
        "press_key":      lambda a: f"Pressing {a.get('key', '')}…",
        "take_screenshot":lambda a: "Taking a screenshot…",
        "find_file":      lambda a: f"🔍 Searching for '{a.get('name', '')}'…",
        "list_files":     lambda a: f"Listing files in {a.get('directory', '~')}…",
        "read_file":      lambda a: f"Reading {a.get('path', '')}…",
        "read_pdf":       lambda a: f"Reading PDF {a.get('path', '')}…",
        "summarize":      lambda a: "Summarizing document…",
        "summarize_codebase": lambda a: f"Summarizing code in {a.get('directory', '')}…",
        "create_dummy_file": lambda a: f"Creating dummy file {a.get('path', '') or 'default'}…",
        "compare_documents": lambda a: (
            f"Comparing {a.get('label_a', 'A')} vs {a.get('label_b', 'B')}…"
        ),
        "compare_pdf_with_dummy": lambda a: (
            f"Comparing PDF summary with dummy ({a.get('pdf_path', '')})…"
        ),
        "compare_open_files": lambda a: (
            f"Opening & comparing {a.get('path_a', 'File A')} vs {a.get('path_b', 'File B')}…"
        ),
        "extract_pdf_to_spreadsheet": lambda a: (
            f"Locating PDF info → spreadsheet"
            + (f" ({a.get('query', '')})" if a.get("query") else "")
            + "…"
        ),
        "write_spreadsheet": lambda a: f"Writing spreadsheet {a.get('path', '')}…",
        "update_presentation_from_document": lambda a: (
            "Updating presentation from document"
            + (f" ({a.get('query', '')})" if a.get("query") else "")
            + "…"
        ),
        "create_presentation_from_document": lambda a: (
            f"Creating presentation from {a.get('source_path', 'document')}…"
        ),
        "organize_files": lambda a: (
            f"Organizing {a.get('directory', 'files')}"
            + (f" ({a.get('instruction', '')[:60]})" if a.get("instruction") else "")
            + "…"
        ),
        "computer_use":   lambda a: f"Controlling computer: {a.get('goal', '')[:80]}…",
        "write_file":     lambda a: f"Writing to {a.get('path', '')}…",
        "delete_file":    lambda a: f"Deleting {a.get('path', '')}…",
        "move_file":      lambda a: f"Moving {a.get('src', '')} → {a.get('dst', '')}…",
        "open_file":      lambda a: f"Opening {a.get('path', '')}…",
        "set_volume":     lambda a: f"Setting volume to {a.get('level', 50)}%…",
    }
    fn = descs.get(tool)
    return fn(args) if fn else f"Running {tool}…"


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = AgentSession(ws)
    log.info("Client connected [%s]", session.session_id)

    try:
        while True:
            msg = await ws.receive()

            # Audio blob → transcribe + run agent
            if "bytes" in msg and msg["bytes"]:
                data = msg["bytes"]
                log.info("[%s] Received %.1f KB audio (%s)", session.session_id, len(data) / 1024, _audio_suffix(data))
                await session.send({"type": "transcribing", "text": "Transcribing…"})

                loop = asyncio.get_event_loop()
                try:
                    audio  = await loop.run_in_executor(None, webm_to_pcm, data)
                    result = await loop.run_in_executor(None, transcribe, audio)
                except Exception as e:
                    log.exception("ASR error")
                    await session.send({"type": "error", "text": f"Transcription failed: {e}"})
                    continue

                await session.send({
                    "type":     "transcribed",
                    "text":     result["text"],
                    "language": result["language"],
                })

                # Start / interrupt task (cancel or change mid-way → popup)
                await session.handle_new_transcript(result)

            # Text control message
            elif "text" in msg and msg["text"]:
                await session.handle_control(msg["text"])

    except WebSocketDisconnect:
        log.info("Client disconnected [%s]", session.session_id)


@app.get("/health")
async def health():
    return {"status": "ok", "whisper": WHISPER_MODEL_ID}


if __name__ == "__main__":
    uvicorn.run("agent.orchestrator:app", host="0.0.0.0", port=8765, reload=False)
