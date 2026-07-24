"""Voice pipeline: speech <-> text only. No authority, no decision-making.

Provides:
- LocalWhisperVoicePipeline: push-to-talk mic + Whisper ASR + optional TTS
- TextModeVoicePipeline: stdin/stdout fallback

Both implement: listen() -> str, speak(str) -> None.
"""
from __future__ import annotations

import os
import re
import sys
import threading
from abc import ABC, abstractmethod


# Bias Whisper toward agent commands (used as initial_prompt).
_WHISPER_PROMPT = (
    "Voice computer commands: open firefox, open google.com, open vscode, "
    "open notepad, create a file with name app.py, search for cats, "
    "list windows, screenshot, quit."
)

# Common ASR mis-hearings → intended command tokens.
_ASR_FIXES = [
    (r"\bfire\s*fox\b", "firefox"),
    (r"\bchrome\b", "chrome"),
    (r"\b(?:v\s*s\s*code|vs\s*code|visual\s*studio\s*code|vs\s*code)\b", "vscode"),
    (r"\bcode\s+studio\b", "vscode"),
    (r"\bnote\s*pad\b", "notepad"),
    (r"\bgoogle\s+dot\s+com\b", "google.com"),
    (r"\bgit\s*hub\b", "github"),
    (r"\bwiki\s*pedia\b", "wikipedia"),
    (r"\bapp\s+dot\s+p\s*y\b", "app.py"),
    (r"\bapp\s+dot\s+py\b", "app.py"),
    (r"\bfile\s+with\s+(?:the\s+)?name\s+", "file with name "),
    (r"\bcreate\s+a\s+file\s+name\b", "create a file named"),
    (r"\bopen\s+the\s+fire\b", "open firefox"),
    (r"\bquit\b|\bexit\b|\bstop\b", "quit"),
]


class VoicePipeline(ABC):
    @abstractmethod
    def listen(self) -> str: ...

    @abstractmethod
    def speak(self, text: str) -> None: ...


class TextModeVoicePipeline(VoicePipeline):
    """Keyboard fallback when no mic/speaker/ASR model is available."""

    def listen(self) -> str:
        return input("[you] > ").strip()

    def speak(self, text: str) -> None:
        print(f"[agent] {text}")

    def confirm(self, prompt: str) -> str:
        print(prompt, end="", flush=True)
        return input().strip()


def _fix_asr_text(text: str) -> str:
    out = text.strip()
    for pattern, repl in _ASR_FIXES:
        out = re.sub(pattern, repl, out, flags=re.I)
    # Collapse whitespace
    out = re.sub(r"\s+", " ", out).strip()
    # Strip trailing punctuation Whisper often adds
    out = out.rstrip(".,!?;:")
    return out


class LocalWhisperVoicePipeline(VoicePipeline):
    """Push-to-talk mic + Whisper + confirm/correct before returning text.

    Controls:
      1. Press ENTER to start recording
      2. Speak
      3. Press ENTER to stop
      4. Confirm: ENTER = accept, or type the correct command

    Env:
      VOICE_AGENT_WHISPER_MODEL   default small.en (more accurate than base/tiny)
      VOICE_AGENT_RECORD_SECONDS  max recording length (default 20)
      VOICE_AGENT_CONFIRM         1 (default) confirm/edit transcript; 0 skip
    """

    def __init__(
        self,
        model_size: str | None = None,
        record_seconds: float | None = None,
        confirm: bool | None = None,
    ):
        try:
            from faster_whisper import WhisperModel
            import sounddevice as sd
            import numpy as np
            import pyttsx3
        except ImportError as e:
            raise ImportError(
                "LocalWhisperVoicePipeline requires 'faster-whisper', "
                "'sounddevice', 'numpy', and 'pyttsx3'."
            ) from e

        self._sd = sd
        self._np = np
        model_size = model_size or os.environ.get("VOICE_AGENT_WHISPER_MODEL", "small.en")
        self.record_seconds = float(
            record_seconds
            if record_seconds is not None
            else os.environ.get("VOICE_AGENT_RECORD_SECONDS", "20")
        )
        if confirm is None:
            confirm = os.environ.get("VOICE_AGENT_CONFIRM", "1") not in ("0", "false", "no")
        self.confirm = confirm
        self.sample_rate = 16000

        print(f"[voice] Loading Whisper '{model_size}' (first run may download)…")
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._tts = None
        try:
            self._tts = pyttsx3.init()
            self._tts.setProperty("rate", 175)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] TTS unavailable ({exc}); replies will be printed only.")

        print("[voice] Push-to-talk ready (rules planner + confirm before run).")
        print("  1) Press ENTER to start recording")
        print("  2) Speak your command clearly and slowly")
        print("  3) Press ENTER to stop")
        print("  4) Type the correct command if [heard] is wrong (recommended)")
        print("  5) Then confirm the plan with yes/no")
        print("  Say 'quit' to exit.\n")

    def _record_push_to_talk(self):
        """Record from ENTER-to-start until ENTER-to-stop (or max seconds)."""
        input("▶ Press ENTER, then speak… ")
        print(
            f"● REC — speak now. Press ENTER when finished "
            f"(max {self.record_seconds:.0f}s)…",
            flush=True,
        )

        frames: list = []
        stop = threading.Event()
        err: list[BaseException] = []

        def callback(indata, _frames, _time, status):
            if status:
                # Non-fatal overflow warnings happen; keep going.
                pass
            if not stop.is_set():
                frames.append(indata.copy())

        try:
            stream = self._sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Mic open failed: {exc}") from exc

        def wait_for_enter():
            try:
                input()
            except EOFError:
                pass
            stop.set()

        waiter = threading.Thread(target=wait_for_enter, daemon=True)
        with stream:
            waiter.start()
            # Also stop if max duration hit
            if not stop.wait(timeout=self.record_seconds):
                stop.set()
                print("[voice] Max recording time reached.")
            waiter.join(timeout=0.2)

        if err:
            raise err[0]
        if not frames:
            return self._np.zeros(0, dtype="float32")
        audio = self._np.concatenate(frames, axis=0).flatten()
        return audio

    def _normalize(self, audio):
        if audio.size == 0:
            return audio
        peak = float(self._np.max(self._np.abs(audio)))
        if peak < 1e-4:
            return audio
        # Soft normalize toward ~0.7 peak for Whisper
        return (audio / peak) * 0.7

    def _transcribe(self, audio) -> tuple[str, float]:
        """Return (text, avg_logprob). Lower avg_logprob ≈ less confident."""
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400),
            beam_size=5,
            best_of=5,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=_WHISPER_PROMPT,
            no_speech_threshold=0.6,
        )
        segs = list(segments)
        raw = " ".join(seg.text for seg in segs).strip()
        if not segs:
            return "", -99.0
        avg_lp = sum(float(seg.avg_logprob) for seg in segs) / len(segs)
        return _fix_asr_text(raw), avg_lp

    def listen(self) -> str:
        try:
            audio = self._record_push_to_talk()
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] Mic error ({exc}). Type your command instead:")
            return input("[you] > ").strip()

        audio = self._normalize(audio)
        peak = float(self._np.max(self._np.abs(audio))) if audio.size else 0.0
        if peak < 0.02:
            print("[voice] No speech detected. Try again closer to the mic.")
            return ""

        print("[voice] Transcribing…", flush=True)
        text, avg_lp = self._transcribe(audio)
        if not text:
            print("[voice] Could not understand. Please TYPE the command:")
            return input("[you type] > ").strip()

        low_conf = avg_lp < -0.7
        print(f"[heard] {text}", flush=True)
        if low_conf:
            print(
                f"[voice] Low confidence (score={avg_lp:.2f}). "
                f"Please TYPE the correct command (do not trust [heard])."
            )
            typed = input("[you type] > ").strip()
            return _fix_asr_text(typed) if typed else ""

        if not self.confirm:
            return text

        # Always prefer a typed correction when Whisper is unsure of wording.
        fix = input(
            "If [heard] is wrong, TYPE the correct command.\n"
            "If it is right, press ENTER: "
        ).strip()
        if not fix:
            return text
        if fix.lower() in {"retry", "r", "again"}:
            return ""
        return _fix_asr_text(fix)

    def confirm(self, prompt: str) -> str:
        """Keyboard confirm for plans — more reliable than another ASR pass."""
        print(prompt, end="", flush=True)
        return input().strip()

    def speak(self, text: str) -> None:
        print(f"[agent] {text}", flush=True)
        if self._tts is None:
            return
        try:
            # Don't speak long plan dumps — print is enough; TTS first line only.
            first = text.split("\n")[0]
            spoken = first if len(first) < 160 else first[:157] + "…"
            self._tts.say(spoken)
            self._tts.runAndWait()
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] TTS failed ({exc})", file=sys.stderr)


def build_voice_pipeline(prefer_audio: bool = True) -> VoicePipeline:
    """Try the real audio backend; fall back to text mode if unavailable."""
    if prefer_audio:
        try:
            return LocalWhisperVoicePipeline()
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] Audio pipeline unavailable ({exc}); using text mode.")
    return TextModeVoicePipeline()
