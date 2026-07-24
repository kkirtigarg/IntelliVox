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
import os
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
from agent.tools import run_tool
from agent.plan_validator import validate_plan
from agent.planner import plan, resolve_step_args, replan
from agent.verifier import verify_step
from agent.audit import AuditSession
from agent.metrics import compute_metrics, group_failures
from agent.telemetry import track

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("intellivox")

WHISPER_MODEL_ID = "Systran/faster-whisper-medium"
SAMPLE_RATE      = 16_000
# Reject non-English speech before planning (set INTELLIVOX_ENGLISH_ONLY=false to disable)
ENGLISH_ONLY = os.getenv("INTELLIVOX_ENGLISH_ONLY", "true").lower() in ("1", "true", "yes")
# Min confidence to treat auto-detected language as non-English (lower = stricter English gate)
NON_ENGLISH_REJECT_PROB = float(os.getenv("INTELLIVOX_NON_ENGLISH_REJECT_PROB", "0.35"))
MAX_REPLANS = int(os.getenv("INTELLIVOX_MAX_REPLANS", "2"))

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
    if ENGLISH_ONLY:
        log.info("✓ English-only mode (set INTELLIVOX_ENGLISH_ONLY=false to allow other languages)")
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
    segments, info = whisper_model.transcribe(audio, beam_size=5)
    lang = info.language
    prob = info.language_probability

    if (
        ENGLISH_ONLY
        and lang != "en"
        and prob >= NON_ENGLISH_REJECT_PROB
    ):
        log.info("Rejected non-English transcript [%s] prob=%.2f", lang, prob)
        return {
            "text": "",
            "language": lang,
            "confidence": round(prob, 2),
            "english_only_rejected": True,
        }

    if ENGLISH_ONLY and lang == "en":
        segments, info = whisper_model.transcribe(audio, beam_size=5, language="en")

    text = " ".join(s.text.strip() for s in segments).strip()
    return {
        "text": text,
        "language": "en" if ENGLISH_ONLY else info.language,
        "confidence": round(info.language_probability, 2),
    }


transcribe = track(name="whisper_transcribe", project_name="intellivox", tags=["asr"])(transcribe)


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

    @track(name="agent_run_task", project_name="intellivox", tags=["agent"])
    async def run_task(self, transcript_result: dict):
        """Full pipeline: transcript → plan → safety → execute → verify."""
        self._cancelled = False  # fresh task — don't inherit cancel from a prior attempt
        text     = transcript_result["text"]
        language = transcript_result["language"]

        if not text:
            await self.send({"type": "error", "text": "I didn't catch that. Please try again."})
            return

        log.info("[%s] Transcript [%s]: %s", self.session_id, language, text)

        # ── 1. Plan ──────────────────────────────────────────────────────────
        await self.send({"type": "planning", "text": "Figuring out your task…"})
        tts.speak("Let me figure that out.")

        loop = asyncio.get_event_loop()
        t0 = time.perf_counter()
        action_plan = await loop.run_in_executor(None, plan, text)
        plan_ms = (time.perf_counter() - t0) * 1000
        self.audit.log_plan(action_plan, duration_ms=plan_ms)

        if action_plan.get("clarification_needed"):
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

        # ── 2. Execute steps (with replan on failure) ────────────────────────
        completed    = 0
        step_results: list[dict] = []
        step_idx     = 0
        replans      = 0
        last_rich_result: dict | None = None

        while step_idx < len(steps):
            if self._cancelled:
                await self.send({"type": "error", "text": "Task cancelled."})
                self.audit.log_outcome("cancelled", "User cancelled the task.")
                tts.speak("Task cancelled.")
                return

            while self._paused and not self._cancelled:
                await asyncio.sleep(0.3)

            step = steps[step_idx]
            tool = step.get("tool", "")
            args = step.get("args", {})
            args = resolve_step_args(args, step_results)

            if tool == "summarize":
                text = str(args.get("text") or "")
                if not text.strip() or "{{step_" in text:
                    err_msg = "Nothing to summarize — email content was not read."
                    await self.send({"type": "step_failed", "step_index": step_idx, "text": err_msg})
                    log.warning("[%s] skipping summarize — no mail body", self.session_id)
                    step_idx += 1
                    continue

            if tool == "compare_summarize":
                if not str(args.get("text_a") or "").strip() or not str(args.get("text_b") or "").strip():
                    err_msg = "Nothing to compare — one or both sources were not read."
                    await self.send({"type": "step_failed", "step_index": step_idx, "text": err_msg})
                    log.warning("[%s] skipping compare_summarize — missing sources", self.session_id)
                    step_idx += 1
                    continue
                if "{{step_" in str(args.get("text_a", "")) or "{{step_" in str(args.get("text_b", "")):
                    err_msg = "Comparison sources were not loaded."
                    await self.send({"type": "step_failed", "step_index": step_idx, "text": err_msg})
                    step_idx += 1
                    continue

            if tool == "read_pdf" and "{{step_" in str(args.get("path", "")):
                err_msg = "PDF path not resolved — file search failed."
                await self.send({"type": "step_failed", "step_index": step_idx, "text": err_msg})
                log.warning("[%s] skipping read_pdf — unresolved path placeholder", self.session_id)
                step_idx += 1
                continue

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
                confirm_msg = f"Step {step_idx+1}: {safety_result.reason} Shall I proceed?"
                await self.send({
                    "type":       "confirm",
                    "text":       confirm_msg,
                    "tool":       tool,
                    "step_index": step_idx,
                })
                tts.speak(confirm_msg)
                self.audit.log_confirmation(tool, "waiting")
                approved = await self.wait_for_confirmation()
                self.audit.log_confirmation(tool, "yes" if approved else "no")
                if not approved:
                    await self.send({"type": "info", "text": "Skipped that step."})
                    tts.speak("Okay, skipping.")
                    step_idx += 1
                    continue

            step_desc = _describe_step(tool, args)
            await self.send({"type": "executing", "step_index": step_idx, "tool": tool, "text": step_desc})
            log.info("[%s] → %s(%s)", self.session_id, tool, args)

            t0 = time.perf_counter()
            result = await loop.run_in_executor(None, run_tool, tool, args)
            tool_ms = (time.perf_counter() - t0) * 1000
            step_results.append(result)

            verification = await loop.run_in_executor(None, verify_step, tool, args, result)
            verified = verification.get("verified", False)
            self.audit.log_action(tool, args, result, verified, step_index=step_idx, duration_ms=tool_ms)

            if result.get("success") and verified:
                if tool == "search_mail" and result.get("count", 0) == 0:
                    result["success"] = False
                    result["message"] = f"No mail found for '{args.get('query', '')}'"

            if result.get("success") and verified:
                done_msg = verification.get("message", f"{tool} done")

                if tool == "find_file" and result.get("path"):
                    found_path = result["path"]
                    done_msg = f"Found: {os.path.basename(found_path)}"
                    log.info("[%s] find_file -> %s", self.session_id, found_path)
                    if step_idx + 1 < len(steps):
                        ns = steps[step_idx + 1]
                        next_tool = ns.get("tool")
                        if next_tool == "summarize_codebase":
                            ns["args"]["directory"] = found_path
                            ns["args"].pop("path", None)
                        elif next_tool in ("open_file", "read_pdf", "read_file"):
                            ns["args"]["path"] = found_path

                elif tool == "find_compare_pdf_pair" and result.get("success"):
                    label_a = result.get("label_a", "PDF 1")
                    label_b = result.get("label_b", "PDF 2")
                    done_msg = f"Matched {label_a} and {label_b}"

                    if result.get("needs_confirm"):
                        confirm_msg = result.get(
                            "message",
                            f"Proceed with {label_a} and {label_b}?",
                        )
                        await self.send({
                            "type":       "confirm",
                            "text":       confirm_msg,
                            "tool":       tool,
                            "step_index": step_idx,
                        })
                        if result.get("fallback_match"):
                            tts.speak(
                                "I could not match exact names. I found two likely PDFs. Please confirm to compare."
                            )
                        else:
                            tts.speak(
                                "I found two PDFs with partial name matches. Please confirm to compare."
                            )
                        self.audit.log_confirmation(tool, "waiting")
                        approved = await self.wait_for_confirmation()
                        self.audit.log_confirmation(tool, "yes" if approved else "no")
                        if not approved:
                            await self.send({"type": "info", "text": "Comparison cancelled."})
                            tts.speak("Okay, cancelled.")
                            break

                    read_paths = [result["path_a"], result["path_b"]]
                    read_i = 0
                    for ns in steps[step_idx + 1:]:
                        nt = ns.get("tool")
                        if nt == "read_pdf":
                            ns["args"]["path"] = read_paths[read_i]
                            read_i += 1
                        elif nt == "compare_summarize":
                            ns["args"]["label_a"] = label_a
                            ns["args"]["label_b"] = label_b
                            break

                elif tool == "read_pdf" and result.get("text"):
                    chars = len(result["text"])
                    done_msg = f"Read {result.get('page_count', '?')} pages ({chars:,} chars)"
                    _feed_compare_source(
                        steps,
                        step_idx,
                        result["text"],
                        os.path.basename(result.get("path", "PDF")),
                    )
                    if step_idx + 1 < len(steps):
                        ns = steps[step_idx + 1]
                        if ns.get("tool") in ("summarize", "answer_question"):
                            ns["args"]["text"] = result["text"]

                elif tool == "open_gmail":
                    done_msg = result.get("message", "Opened Gmail in Chrome")

                elif tool in ("read_mail", "read_gmail") and result.get("body"):
                    n_read = result.get("count", 1)
                    if result.get("source") == "apple_mail":
                        done_msg = f"Read {n_read} emails via Apple Mail (Gmail open in Chrome)"
                    elif n_read > 1:
                        done_msg = f"Read {n_read} emails"
                    else:
                        done_msg = f"Read mail: {result.get('subject', '')[:60]}"
                    _feed_compare_source(steps, step_idx, result["body"], "Gmail inbox")
                    if step_idx + 1 < len(steps):
                        ns = steps[step_idx + 1]
                        if ns.get("tool") in ("summarize", "answer_question"):
                            ns["args"]["text"] = result["body"]

                elif tool == "search_mail":
                    done_msg = f"Found {result.get('count', 0)} message(s)"

                elif tool == "web_browse" and result.get("text"):
                    done_msg = result.get("message", "Page loaded")
                    if step_idx + 1 < len(steps):
                        ns = steps[step_idx + 1]
                        if ns.get("tool") in ("summarize", "answer_question"):
                            ns["args"]["text"] = result["text"]

                elif tool in ("summarize", "summarize_codebase", "compare_summarize") and result.get("summary"):
                    if tool == "summarize_codebase":
                        done_msg = f"Summarized {result.get('files_read', '?')} files"
                    elif tool == "compare_summarize":
                        done_msg = f"Compared {result.get('label_a', 'A')} vs {result.get('label_b', 'B')}"
                    else:
                        done_msg = "Summary complete"
                    title = "Comparison" if tool == "compare_summarize" else "Summary"
                    last_rich_result = {"label": title, "text": result["summary"]}
                    await self.send({
                        "type":  "rich_result",
                        "label": title,
                        "text":  result["summary"],
                    })
                    short = result["summary"][:220].replace("\n", " ").strip()
                    tts.speak(short)
                    if step_idx + 1 < len(steps):
                        ns = steps[step_idx + 1]
                        if ns.get("tool") == "save_summary_file":
                            ns["args"]["summary"] = result["summary"]
                            if result.get("directory") and not ns["args"].get("filename"):
                                base = os.path.basename(result["directory"])
                                ns["args"]["filename"] = f"{base}-codebase-summary.txt"

                elif tool == "save_summary_file" and result.get("path"):
                    done_msg = result.get("message", "Summary saved")
                    tts.speak(f"Summary saved to {os.path.basename(result['path'])}")

                elif tool == "answer_question" and result.get("answer"):
                    done_msg = "Answer ready"
                    await self.send({"type": "rich_result", "label": "Answer", "text": result["answer"]})
                    short = result["answer"][:220].replace("\n", " ").strip()
                    tts.speak(short)

                if result.get("success") and verified:
                    step_done_msg: dict = {
                        "type":       "step_done",
                        "step_index": step_idx,
                        "verified":   True,
                        "text":       done_msg,
                    }
                    if last_rich_result and tool in (
                        "summarize", "summarize_codebase", "compare_summarize",
                    ):
                        step_done_msg["summary"] = last_rich_result["text"]
                        step_done_msg["summary_label"] = last_rich_result["label"]
                    await self.send(step_done_msg)
                    log.info("[%s] step %d done: %s", self.session_id, step_idx, done_msg)
                    completed += 1
                    step_idx += 1
                    continue

            err_msg = result.get("message") or verification.get("message", "Step failed")
            await self.send({"type": "step_failed", "step_index": step_idx, "text": err_msg})
            log.warning("[%s] step %d failed: %s", self.session_id, step_idx, err_msg)
            self.audit.log_error(err_msg)

            if tool == "read_gmail":
                while step_idx + 1 < len(steps):
                    nxt = steps[step_idx + 1].get("tool")
                    if nxt in ("summarize", "save_summary_file", "read_gmail", "open_gmail", "compare_summarize"):
                        steps.pop(step_idx + 1)
                    else:
                        break
                step_idx += 1
                continue

            if tool == "find_compare_pdf_pair":
                while step_idx + 1 < len(steps):
                    nxt = steps[step_idx + 1].get("tool")
                    if nxt in ("read_pdf", "compare_summarize"):
                        steps.pop(step_idx + 1)
                    else:
                        break

            if replans < MAX_REPLANS:
                replans += 1
                context = (
                    f"Original request: {text}\n"
                    f"Failed step {step_idx + 1}: tool={tool}, error={err_msg}\n"
                    f"Completed {completed} steps successfully.\n"
                    f"Recent results: {json.dumps(step_results[-2:], default=str)[:1500]}"
                )
                await self.send({"type": "planning", "text": "Adjusting plan after failure…"})
                new_plan = await loop.run_in_executor(None, replan, text, context)
                self.audit.log_plan(new_plan, duration_ms=0)
                if new_plan.get("clarification_needed"):
                    q = new_plan.get("clarification_question") or err_msg
                    await self.send({"type": "error", "text": q})
                    tts.speak(q[:200])
                    break
                if new_plan.get("steps") and not new_plan.get("clarification_needed"):
                    await self.send({
                        "type":        "plan",
                        "intent":      new_plan.get("intent", "replan"),
                        "explanation": new_plan.get("explanation", "Trying a different approach…"),
                        "steps":       new_plan.get("steps", []),
                    })
                    steps = steps[:step_idx] + new_plan["steps"]
                    continue

            step_idx += 1

        # Done
        summary_msg = f"Done! Completed {completed} of {len(steps)} steps."
        done_payload: dict = {"type": "done", "text": summary_msg}
        if last_rich_result:
            done_payload["summary"] = last_rich_result["text"]
            done_payload["summary_label"] = last_rich_result["label"]
        await self.send(done_payload)
        if completed == len(steps):
            tts.speak("All done!")
        self.audit.log_outcome("success" if completed == len(steps) else "partial", summary_msg)
        log.info("[%s] Task complete: %s", self.session_id, summary_msg)


def _feed_compare_source(steps: list, from_idx: int, text: str, label: str) -> None:
    """Inject read content into the next compare_summarize step."""
    for step in steps[from_idx + 1:]:
        if step.get("tool") != "compare_summarize":
            continue
        args = step.setdefault("args", {})
        if not args.get("text_a"):
            args["text_a"] = text
            args["label_a"] = label
        elif not args.get("text_b"):
            args["text_b"] = text
            args["label_b"] = label
        return


def _describe_step(tool: str, args: dict) -> str:
    descs = {
        "open_browser":   lambda a: f"Opening {a.get('browser', 'browser').title()}…",
        "navigate_url":   lambda a: f"Navigating to {a.get('url', '')}…",
        "google_search":  lambda a: f"Searching Google for '{a.get('query', '')}'…",
        "youtube_search": lambda a: f"Searching YouTube for '{a.get('query', '')}'…",
        "youtube_play":   lambda a: f"Playing '{a.get('query', '')}' on YouTube…",
        "open_app":       lambda a: f"Opening {a.get('name', 'app')}…",
        "close_app":      lambda a: f"Closing {a.get('name', 'app')}…",
        "type_text":      lambda a: f"Typing: {a.get('text', '')}…",
        "press_key":      lambda a: f"Pressing {a.get('key', '')}…",
        "take_screenshot":lambda a: "Taking a screenshot…",
        "find_file":      lambda a: f"🔍 Searching for '{a.get('name', '')}'…",
        "find_compare_pdf_pair": lambda a: (
            f"🔍 Matching PDFs '{a.get('name_a', '')}' and '{a.get('name_b', '')}'…"
        ),
        "list_files":     lambda a: f"Listing files in {a.get('directory', '~')}…",
        "read_file":      lambda a: f"Reading {a.get('path', '')}…",
        "summarize_codebase": lambda a: f"Summarizing code in {a.get('directory', '')}…",
        "save_summary_file": lambda a: f"Saving summary to {a.get('filename', 'summary.txt')}…",
        "open_gmail":     lambda a: (
            f"Opening Gmail in Chrome"
            + (f" (search: {a.get('query', '')})" if a.get("query") else "…")
        ),
        "read_gmail":     lambda a: f"Reading top {a.get('limit', 5)} Gmail messages…",
        "compare_summarize": lambda a: (
            f"Comparing {a.get('label_a', 'source A')} and {a.get('label_b', 'source B')}…"
        ),
        "web_browse":     lambda a: f"Browsing web: {(a.get('goal') or a.get('url', ''))[:60]}…",
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
                session._cancelled = False  # new voice command starts a fresh task
                log.info("[%s] Received %.1f KB audio (%s)", session.session_id, len(data) / 1024, _audio_suffix(data))
                await session.send({"type": "transcribing", "text": "Transcribing…"})

                loop = asyncio.get_event_loop()
                try:
                    t0 = time.perf_counter()
                    audio  = await loop.run_in_executor(None, webm_to_pcm, data)
                    result = await loop.run_in_executor(None, transcribe, audio)
                    asr_ms = (time.perf_counter() - t0) * 1000
                except Exception as e:
                    log.exception("ASR error")
                    await session.send({"type": "error", "text": f"Transcription failed: {e}"})
                    continue

                await session.send({
                    "type":     "transcribed",
                    "text":     result["text"],
                    "language": result["language"],
                })
                session.audit.log_transcript(result["text"], result["language"], duration_ms=asr_ms)

                if result.get("english_only_rejected"):
                    msg = (
                        "IntelliVox is set to English only. "
                        "Please speak in English and try again."
                    )
                    await session.send({"type": "clarify", "text": msg})
                    tts.speak("Please speak in English.")
                    continue

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


@app.get("/metrics")
async def metrics():
    """Aggregate session metrics from audit logs (Comet-style monitoring)."""
    from agent.telemetry import opik_enabled
    data = compute_metrics()
    data["opik_tracing"] = opik_enabled()
    return data


@app.get("/diagnostics")
async def diagnostics():
    """Grouped recurring errors from audit logs."""
    return {"failures": group_failures()}


if __name__ == "__main__":
    uvicorn.run("agent.orchestrator:app", host="0.0.0.0", port=8765, reload=False)
