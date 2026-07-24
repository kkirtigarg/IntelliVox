"""
Live microphone transcription using Systran/faster-whisper-medium.

Usage:
  python3 test_whisper.py          # records until you press Enter, then transcribes
  python3 test_whisper.py --live   # continuous mode: auto-detects silence between phrases
  python3 test_whisper.py --debug  # show mic RMS levels to tune silence threshold
"""

import sys
import time
import threading
import queue
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE     = 16000
SILENCE_THRESH  = 0.02   # RMS below this = silence (run --debug to tune)
SILENCE_SECONDS = 1.5    # pause this long → transcribe
MAX_PHRASE_SEC  = 10     # force-flush after this many seconds of speech
CHUNK_SEC       = 0.3    # chunk size in seconds


# ── model loader ──────────────────────────────────────────────────────────────

def load_model():
    print("Loading Systran/faster-whisper-medium model (downloads ~1.5 GB on first run) ...")
    t0 = time.time()
    model = WhisperModel(
        "Systran/faster-whisper-medium",
        device="auto",
        compute_type="int8",
    )
    print(f"Model ready in {time.time() - t0:.1f}s\n")
    return model


# ── single-shot mode ──────────────────────────────────────────────────────────

def record_until_enter() -> np.ndarray:
    chunks = []

    def callback(indata, frames, time_info, status):
        chunks.append(indata.copy())

    print("Recording ... press  Enter  to stop and transcribe.")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        callback=callback):
        input()

    audio = np.concatenate(chunks, axis=0).flatten()
    print(f"Captured {len(audio)/SAMPLE_RATE:.1f}s of audio.\n")
    return audio


def transcribe_once(model, audio):
    print("Transcribing ...")
    segments, info = model.transcribe(audio, beam_size=5, language="en")
    print(f"Language: {info.language!r} ({info.language_probability:.0%})\n")
    print("─" * 50)
    full = []
    for seg in segments:
        text = seg.text.strip()
        print(f"[{seg.start:5.2f}s → {seg.end:5.2f}s]  {text}")
        full.append(text)
    print("─" * 50)
    print("\nFull text:\n" + " ".join(full))


# ── debug mode — shows mic RMS so you can tune SILENCE_THRESH ─────────────────

def debug_mode():
    print("Debug mode — watch the RMS bar. Speech should be well above the threshold.")
    print(f"Current SILENCE_THRESH = {SILENCE_THRESH}  |  Ctrl+C to quit\n")

    def callback(indata, frames, time_info, status):
        rms = float(np.sqrt(np.mean(indata ** 2)))
        bar = "█" * int(rms * 500)
        label = "SPEECH " if rms >= SILENCE_THRESH else "silence"
        print(f"\r[{label}]  RMS={rms:.4f}  {bar:<40}", end="", flush=True)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=int(SAMPLE_RATE * CHUNK_SEC), callback=callback):
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nDone.")


# ── continuous / live mode ────────────────────────────────────────────────────

def live_mode(model):
    audio_q: queue.Queue = queue.Queue()
    chunk_size = int(SAMPLE_RATE * CHUNK_SEC)
    silence_limit = int(SILENCE_SECONDS / CHUNK_SEC)
    max_chunks    = int(MAX_PHRASE_SEC   / CHUNK_SEC)

    def callback(indata, frames, time_info, status):
        audio_q.put(indata.copy().flatten())

    print("Listening ... speak and pause to transcribe. Ctrl+C to quit.\n")
    print("(If nothing happens, run with --debug first to check your mic level)\n")
    print("─" * 50)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=chunk_size, callback=callback):

        buf: list[np.ndarray] = []   # all audio (speech + silence) since last flush
        silent_chunks = 0
        has_speech = False

        try:
            while True:
                chunk = audio_q.get()
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                buf.append(chunk)

                if rms >= SILENCE_THRESH:
                    has_speech = True
                    silent_chunks = 0
                    print(f"\r Mic: {'█' * min(int(rms*300), 30):<30}", end="", flush=True)
                else:
                    silent_chunks += 1

                flush = has_speech and (
                    silent_chunks >= silence_limit or len(buf) >= max_chunks
                )

                if flush:
                    print("\r" + " " * 50 + "\r", end="")  # clear mic bar
                    audio = np.concatenate(buf)
                    buf.clear()
                    silent_chunks = 0
                    has_speech = False

                    segments, info = model.transcribe(audio, beam_size=5, language="en")
                    text = " ".join(s.text.strip() for s in segments).strip()
                    if text:
                        print(f"[{info.language}]  {text}")

        except KeyboardInterrupt:
            print("\nStopped.")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--debug" in sys.argv:
        debug_mode()
    else:
        model = load_model()
        if "--live" in sys.argv:
            live_mode(model)
        else:
            audio = record_until_enter()
            transcribe_once(model, audio)
