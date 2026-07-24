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

Message protocol (client → server):
  Binary: raw audio blob (webm/opus) → triggers transcription + agent
  Text:   "confirm:yes"  or  "confirm:no"  → for confirmation prompts
  Text:   "cancel"  → abort current task
  Text:   "pause"   → pause after current step
  Text:   "resume"  → resume paused task
"""

import asyncio
import json
import logging
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


def _is_clarification_needed(val) -> bool:
    """Return True only when the LLM actually wants clarification.
    Handles bool True/False and string 'true'/'false' from the LLM JSON."""
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() not in ("false", "0", "no", "null", "none", "")


def _has_unresolved_placeholders(args: dict) -> bool:
    """Return True if any {{step_N_result.field}} placeholder remains after resolution."""
    import re as _re
    return bool(_re.search(r'\{\{step_\d+_result\.\w+\}\}', json.dumps(args)))

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
    segments, info = whisper_model.transcribe(audio, beam_size=5, language="en")
    text = " ".join(s.text.strip() for s in segments).strip()
    return {"text": text, "language": info.language, "confidence": round(info.language_probability, 2)}


# ── Agent execution loop ───────────────────────────────────────────────────────

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

    async def send(self, msg: dict):
        await self.ws.send_text(json.dumps(msg))

    async def handle_control(self, text: str):
        """Handle control messages from the client."""
        text = text.strip().lower()
        if text == "cancel":
            self._cancelled = True
            log.info("[%s] Task cancelled by user", self.session_id)
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
        """Wait until the user sends confirm:yes or confirm:no."""
        self._confirm_event.clear()
        self._confirm_result = None
        try:
            await asyncio.wait_for(self._confirm_event.wait(), timeout=30.0)
            return bool(self._confirm_result)
        except asyncio.TimeoutError:
            return False  # treat timeout as "no"

    async def run_task(self, transcript_result: dict):
        """Full pipeline: transcript → plan → safety → execute → verify."""
        text     = transcript_result["text"]
        language = transcript_result["language"]

        if not text:
            await self.send({"type": "error", "text": "I didn't catch that. Please try again."})
            return

        self.audit.log_transcript(text, language)
        log.info("[%s] Transcript [%s]: %s", self.session_id, language, text)

        # ── 1. Plan ──────────────────────────────────────────────────────────
        await self.send({"type": "planning", "text": "Figuring out your task…"})
        tts.speak("Let me figure that out.")

        loop = asyncio.get_event_loop()
        action_plan = await loop.run_in_executor(None, plan, text)
        self.audit.log_plan(action_plan)

        if _is_clarification_needed(action_plan.get("clarification_needed")):
            q = action_plan.get("clarification_question", "Could you rephrase that?")
            await self.send({"type": "clarify", "text": q})
            tts.speak(q)
            self.audit.log_outcome("clarification", q)
            return

        await self.send({
            "type":        "plan",
            "intent":      action_plan.get("intent", ""),
            "explanation": action_plan.get("explanation", ""),
            "steps":       action_plan.get("steps", []),
        })

        steps = action_plan.get("steps", [])
        if not steps:
            await self.send({"type": "error", "text": "I couldn't figure out what to do."})
            return

        # ── 2. Execute steps ─────────────────────────────────────────────────
        completed    = 0
        step_results = []   # stores each step's raw result for chaining
        for i, step in enumerate(steps):
            if self._cancelled:
                await self.send({"type": "info", "text": "Task cancelled."})
                self.audit.log_outcome("cancelled", "User cancelled the task.")
                tts.speak("Task cancelled.")
                return

            # Wait while paused
            while self._paused and not self._cancelled:
                await asyncio.sleep(0.3)

            tool = step.get("tool", "")
            args = step.get("args", {})

            # ── Resolve placeholders from previous step results (chaining) ────
            args = resolve_step_args(args, step_results)

            # Skip step if a required prior step failed and its result is missing
            if _has_unresolved_placeholders(args):
                await self.send({"type": "step_failed", "step_index": i,
                                 "text": "Skipped: a required previous step did not produce a result."})
                step_results.append({"success": False})
                log.warning("[%s] step %d skipped — unresolved placeholders", self.session_id, i)
                continue

            # ── Safety check ─────────────────────────────────────────────────
            safety_result = safety.check(tool, args, source="voice")
            self.audit.log_safety(tool, args, safety_result.decision, safety_result.reason)
            log.info("[%s] Safety %s → %s", self.session_id, tool, safety_result.decision)

            if safety_result.decision == safety.Decision.BLOCK:
                msg = f"I can't do that: {safety_result.reason}"
                await self.send({"type": "safety_block", "text": msg})
                tts.speak(msg)
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

                if not approved:
                    await self.send({"type": "info", "text": "Skipped that step."})
                    tts.speak("Okay, skipping.")
                    continue

            # ── Execute ───────────────────────────────────────────────────────
            step_desc = _describe_step(tool, args)
            await self.send({"type": "executing", "step_index": i, "tool": tool, "text": step_desc})
            log.info("[%s] → %s(%s)", self.session_id, tool, args)

            result = await loop.run_in_executor(None, run_tool, tool, args)

            # Store result for chaining (next steps can reference it)
            step_results.append(result)

            # ── Verify ────────────────────────────────────────────────────────
            verification = await loop.run_in_executor(None, verify_step, tool, args, result)
            verified = verification.get("verified", False)
            self.audit.log_action(tool, args, result, verified)

            if result.get("success") and verified:
                done_msg = verification.get("message", f"{tool} done")

                # find_file: inject path into next pdf/file step
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

                # read_pdf: inject text into next summarize/answer step
                elif tool == "read_pdf" and result.get("text"):
                    chars = len(result["text"])
                    done_msg = f"Read {result.get('page_count','?')} pages ({chars:,} chars)"
                    if i + 1 < len(steps):
                        ns = steps[i + 1]
                        if ns.get("tool") in ("summarize", "answer_question"):
                            ns["args"]["text"] = result["text"]

                # summarize / summarize_codebase: display full summary in UI + speak excerpt
                elif tool in ("summarize", "summarize_codebase") and result.get("summary"):
                    if tool == "summarize_codebase":
                        done_msg = f"Summarized {result.get('files_read', '?')} files"
                    else:
                        done_msg = "Summary complete"
                    await self.send({"type": "rich_result", "label": "Summary", "text": result["summary"]})
                    short = result["summary"][:220].replace("\n", " ").strip()
                    tts.speak(short)

                # answer_question: display answer in UI + speak excerpt
                elif tool == "answer_question" and result.get("answer"):
                    done_msg = "Answer ready"
                    await self.send({"type": "rich_result", "label": "Answer", "text": result["answer"]})
                    short = result["answer"][:220].replace("\n", " ").strip()
                    tts.speak(short)

                # organize_files: display what was moved
                elif tool == "organize_files" and result.get("details") is not None:
                    moved   = result.get("moved", 0)
                    folders = result.get("folders_created", [])
                    done_msg = result.get("message", f"Organised {moved} file(s)")
                    detail_text = "\n".join(result["details"]) if result["details"] else "No files to move."
                    await self.send({"type": "rich_result", "label": "Organised", "text": detail_text})
                    speak_msg = f"Done. Moved {moved} file{'s' if moved != 1 else ''}"
                    if folders:
                        speak_msg += f" and created {len(folders)} folder{'s' if len(folders) != 1 else ''}"
                    tts.speak(speak_msg)

                await self.send({"type": "step_done", "step_index": i, "verified": True, "text": done_msg})
                log.info("[%s] step %d done: %s", self.session_id, i, done_msg)
                completed += 1
            else:
                err_msg = result.get("message") or verification.get("message", "Step failed")
                await self.send({"type": "step_failed", "step_index": i, "text": err_msg})
                log.warning("[%s] step %d failed: %s", self.session_id, i, err_msg)
                self.audit.log_error(err_msg)

        # Done
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
        "summarize_codebase": lambda a: f"Summarizing code in {a.get('directory', '')}…",
        "computer_use":   lambda a: f"Controlling computer: {a.get('goal', '')[:80]}…",
        "write_file":     lambda a: f"Writing to {a.get('path', '')}…",
        "delete_file":    lambda a: f"Deleting {a.get('path', '')}…",
        "move_file":      lambda a: f"Moving {a.get('src', '')} → {a.get('dst', '')}…",
        "open_file":      lambda a: f"Opening {a.get('path', '')}…",
        "organize_files": lambda a: f"Organising {a.get('directory', '')} by {a.get('by', 'type')}…",
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

                # Run agent in background so control messages can still arrive
                asyncio.create_task(session.run_task(result))

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
