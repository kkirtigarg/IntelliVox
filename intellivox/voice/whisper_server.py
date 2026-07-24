"""
IntelliVox – Whisper WebSocket Server
======================================
Connects the React UI mic to faster-whisper transcription.

Flow:
  Browser → records audio via MediaRecorder (webm/opus)
           → sends binary blob over WebSocket
  Server  → converts to float32 PCM via ffmpeg
           → runs faster-whisper
           → sends back JSON  { "transcript": "...", "language": "en" }

Run:
  cd /Users/shivamjaiswal/Desktop/IntelliVox/voice
  .venv/bin/python whisper_server.py
"""

import asyncio
import io
import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("intellivox")

# ── model (loaded once at startup) ───────────────────────────────────────────
MODEL_ID      = "Systran/faster-whisper-medium"
SAMPLE_RATE   = 16_000

model: WhisperModel | None = None


def load_model() -> WhisperModel:
    log.info("Loading %s …", MODEL_ID)
    t0 = time.time()
    m = WhisperModel(MODEL_ID, device="auto", compute_type="int8")
    log.info("Model ready in %.1fs", time.time() - t0)
    return m


# ── audio helpers ─────────────────────────────────────────────────────────────

def webm_to_pcm(data: bytes) -> np.ndarray:
    """Convert any browser audio blob (webm/opus/wav) → float32 mono 16 kHz."""
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    cmd = [
        "ffmpeg", "-y",
        "-i", tmp_path,
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-f", "f32le",
        "-",            # write raw float32 to stdout
    ]
    result = subprocess.run(cmd, capture_output=True)
    Path(tmp_path).unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()}")

    return np.frombuffer(result.stdout, dtype=np.float32)


def transcribe(audio: np.ndarray) -> dict:
    segments, info = model.transcribe(audio, beam_size=5)
    text = " ".join(s.text.strip() for s in segments).strip()
    return {
        "transcript": text,
        "language":   info.language,
        "confidence": round(info.language_probability, 2),
    }


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="IntelliVox Whisper Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    global model
    # Load in a thread so the event loop stays responsive
    model = await asyncio.get_event_loop().run_in_executor(None, load_model)
    log.info("Server ready – ws://localhost:8765/ws")


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_ID}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("Client connected")

    try:
        while True:
            # ── receive audio blob sent by the browser ──
            data = await ws.receive_bytes()
            log.info("Received %.1f KB", len(data) / 1024)

            await ws.send_text(json.dumps({"status": "transcribing"}))

            try:
                loop = asyncio.get_event_loop()
                # Run CPU-heavy work in thread pool
                audio = await loop.run_in_executor(None, webm_to_pcm, data)
                result = await loop.run_in_executor(None, transcribe, audio)
                log.info("→ [%s] %s", result["language"], result["transcript"])
                await ws.send_text(json.dumps({"status": "done", **result}))
            except Exception as exc:
                log.exception("Transcription error")
                await ws.send_text(json.dumps({"status": "error", "message": str(exc)}))

    except WebSocketDisconnect:
        log.info("Client disconnected")


# ── entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "whisper_server:app",
        host="0.0.0.0",
        port=8765,
        ws_ping_interval=30,
        ws_ping_timeout=60,
    )
