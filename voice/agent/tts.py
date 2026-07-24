"""
agent/tts.py
Local text-to-speech using pyttsx3 (no network required).
"""
import logging
import threading

log = logging.getLogger("intellivox.tts")
_engine = None
_lock   = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        try:
            import pyttsx3
            _engine = pyttsx3.init()
            _engine.setProperty("rate",   185)   # words per minute
            _engine.setProperty("volume", 0.9)
            # Try to use a natural-sounding voice
            voices = _engine.getProperty("voices")
            for v in voices:
                if "samantha" in v.name.lower() or "karen" in v.name.lower():
                    _engine.setProperty("voice", v.id)
                    break
        except Exception as e:
            log.warning("TTS init failed: %s", e)
    return _engine


def speak(text: str, blocking: bool = False) -> None:
    """Speak text aloud using the local TTS engine."""
    def _run():
        with _lock:
            engine = _get_engine()
            if engine is None:
                log.warning("TTS not available, skipping: %r", text)
                return
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                log.warning("TTS speak error: %s", e)

    if blocking:
        _run()
    else:
        t = threading.Thread(target=_run, daemon=True)
        t.start()


def stop() -> None:
    """Stop any ongoing speech."""
    with _lock:
        engine = _get_engine()
        if engine:
            try:
                engine.stop()
            except Exception:
                pass
