"""
agent/llm.py
Shared Ollama settings for Llama 3.2 (lightweight-PC friendly).

Default model: llama3.2:1b
Override with env:  INTELLIVOX_LLM_MODEL=llama3.2:3b
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("intellivox.llm")

# Prefer the small 3.2 variant — runs on machines that can't load llama3.1
DEFAULT_MODEL = "llama3.2:1b"
PREFERRED_MODELS = (
    "llama3.2:1b",
    "llama3.2",
    "llama3.2:3b",
    "llama3.2:latest",
)

# Smaller context / input for 1B-class models
NUM_CTX = int(os.environ.get("INTELLIVOX_NUM_CTX", "4096"))
MAX_CHARS = int(os.environ.get("INTELLIVOX_MAX_CHARS", "4000"))

_MODEL: str | None = None


def _list_local_models() -> set[str]:
    try:
        import ollama
        data = ollama.list()
        models = data.get("models", data) if isinstance(data, dict) else getattr(data, "models", [])
        names: set[str] = set()
        for m in models or []:
            name = m.get("name") if isinstance(m, dict) else getattr(m, "model", None) or getattr(m, "name", None)
            if name:
                names.add(str(name))
                # also keep bare tag without :latest
                if name.endswith(":latest"):
                    names.add(name.rsplit(":", 1)[0])
        return names
    except Exception as e:
        log.debug("Could not list Ollama models: %s", e)
        return set()


def resolve_model(preferred: str | None = None) -> str:
    """
    Pick a Llama 3.2-compatible model.
    Order: explicit arg → env → installed preferred tags → DEFAULT_MODEL.
    """
    env_model = (os.environ.get("INTELLIVOX_LLM_MODEL") or "").strip()
    candidate = (preferred or env_model or "").strip()

    installed = _list_local_models()
    if candidate:
        if not installed or candidate in installed or any(
            n.startswith(candidate.split(":")[0]) for n in installed
        ):
            return candidate
        log.warning("Requested model %r not in ollama list; trying Llama 3.2 fallbacks", candidate)

    for name in PREFERRED_MODELS:
        if name in installed:
            return name
        # match family prefix e.g. llama3.2:1b-instruct-q4_K_M
        for inst in installed:
            if inst == name or inst.startswith(name.split(":")[0] + ":"):
                if "3.2" in inst:
                    return inst

    return candidate or DEFAULT_MODEL


def get_model() -> str:
    global _MODEL
    if _MODEL is None:
        _MODEL = resolve_model()
        log.info("Using Ollama LLM model: %s", _MODEL)
    return _MODEL


def set_model(model_name: str) -> None:
    global _MODEL
    _MODEL = model_name.strip()
    os.environ["INTELLIVOX_LLM_MODEL"] = _MODEL
    log.info("LLM model set to: %s", _MODEL)


def chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.3,
    format: str | None = None,
    num_predict: int | None = None,
) -> str:
    """
    Call Ollama chat with Llama 3.2-friendly options.
    Returns assistant message content (string).
    """
    import ollama

    opts: dict[str, Any] = {
        "temperature": temperature,
        "num_ctx": NUM_CTX,
    }
    if num_predict is not None:
        opts["num_predict"] = num_predict

    kwargs: dict[str, Any] = {
        "model": model or get_model(),
        "messages": messages,
        "options": opts,
    }
    if format:
        kwargs["format"] = format

    response = ollama.chat(**kwargs)
    msg = response.get("message") if isinstance(response, dict) else response["message"]
    content = (msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)) or ""
    return str(content).strip()
