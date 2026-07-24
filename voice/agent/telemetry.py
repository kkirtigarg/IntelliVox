"""
agent/telemetry.py
Optional Opik (Comet) tracing + in-memory session metrics.

Configure Opik (pick one):
  opik configure                    # Comet cloud or self-hosted
  export OPIK_USE_LOCAL=true        # local Opik server
  export OPIK_DISABLED=true         # disable tracing entirely

Docs: https://www.comet.com/docs/opik/
"""
from __future__ import annotations

import logging
import os
from functools import wraps

log = logging.getLogger("intellivox.telemetry")

OPIK_DISABLED = os.getenv("OPIK_DISABLED", "").lower() in ("1", "true", "yes")
OPIK_USE_LOCAL = os.getenv("OPIK_USE_LOCAL", "").lower() in ("1", "true", "yes")
OPIK_ENABLED = os.getenv("OPIK_ENABLED", "").lower() in ("1", "true", "yes")
OPIK_URL = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
OPIK_WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
_track = None
_opik = None

# Opt-in only: set OPIK_USE_LOCAL=true or OPIK_ENABLED=true (after opik configure)
if not OPIK_DISABLED and (OPIK_USE_LOCAL or OPIK_ENABLED):
    try:
        import opik as _opik
        from opik import track as _track_fn

        if OPIK_USE_LOCAL:
            _opik.configure(
                use_local=True,
                url=OPIK_URL,
                workspace=OPIK_WORKSPACE,
            )
            log.info("Opik tracing enabled (local: %s)", OPIK_URL)
        else:
            log.info("Opik tracing enabled (cloud / ~/.opik.config)")
        _track = _track_fn
    except Exception as e:
        log.warning("Opik not available (%s) — tracing disabled", e)
else:
    log.debug("Opik tracing off (set OPIK_USE_LOCAL or OPIK_ENABLED to enable)")


def track(*args, **kwargs):
    """No-op decorator when Opik is unavailable."""
    if _track is not None:
        return _track(*args, **kwargs)

    def _decorator(fn):
        return fn

    if args and callable(args[0]) and not kwargs:
        return args[0]
    return _decorator


def opik_enabled() -> bool:
    return _track is not None and not OPIK_DISABLED
