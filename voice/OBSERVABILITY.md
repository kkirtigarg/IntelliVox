# Observability & Evals

IntelliVox tracing uses [Comet Opik](https://www.comet.com/docs/opik/) — open-source LLM/agent observability from Comet. **Opik is opt-in**; the agent runs fine with `OPIK_DISABLED=true` (default in `.env.example`).

---

## Opik local (recommended for dev)

### Step 1 — Start Opik with Docker

Requires **Docker Desktop** ([local deployment guide](https://www.comet.com/docs/opik/self-host/local_deployment)).

```bash
# Option A: helper script
cd voice
chmod +x scripts/start-opik-local.sh
./scripts/start-opik-local.sh

# Option B: manual
git clone https://github.com/comet-ml/opik.git ~/opik
cd ~/opik && ./opik.sh
```

Open the dashboard: **http://localhost:5173**

> **Port conflict:** Opik UI uses port `5173`, same as `npm run dev` in `intellivox-ui/`. Don't run both, or change Vite port in `intellivox-ui/vite.config.js`.

### Step 2 — Configure IntelliVox

```bash
cd voice
source .venv/bin/activate
pip install opik   # already in requirements.txt

cp .env.example .env
# Edit .env:
#   OPIK_DISABLED=          (unset or false)
#   OPIK_USE_LOCAL=true
#   OPIK_URL_OVERRIDE=http://localhost:5173/api
#   OPIK_WORKSPACE=default

opik configure --use_local --yes
```

### Step 3 — Run backend

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)   # or use direnv
python -m agent.orchestrator
```

Use a voice command — traces appear in Opik under project **`intellivox`**.

### Step 4 — Verify

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/metrics    # opik_tracing: true when enabled
curl http://127.0.0.1:8765/diagnostics
```

Stop Opik: `cd ~/opik && ./opik.sh --stop`

---

## Opik cloud (alternative)

```bash
pip install opik
opik configure              # API key + workspace from comet.com
# In .env: unset OPIK_DISABLED, set OPIK_ENABLED=true
python -m agent.orchestrator
```

View traces at [comet.com](https://www.comet.com/).

---

## Disable Opik (default)

```bash
export OPIK_DISABLED=true
```

Or leave `OPIK_DISABLED=true` in `voice/.env`.

---

## What gets traced

| Span | Where |
|------|--------|
| `whisper_transcribe` | Orchestrator ASR |
| `planner_plan` | Ollama + deterministic routers |
| `summarize` / `summarize_codebase` | Document tools |
| `compare_summarize` | PDF/mail comparison |
| `computer_use` | Vision agent loop |
| `agent_run_task` | Full voice pipeline |

Decorators live in `agent/telemetry.py` (`@track(...)`).

---

## Local metrics (no Opik)

```bash
curl http://127.0.0.1:8765/metrics      # aggregates from audit_logs/
curl http://127.0.0.1:8765/diagnostics  # ffmpeg, ollama, playwright, etc.
python scripts/diagnostics.py
python -m evals.run                     # planner routing regression
python -m evals.run --verbose
```

**Audit logs:** `voice/audit_logs/<session_id>.jsonl`

Each session records:
- `plan` — intent, steps, explanation
- `safety` — tool, decision (allow/confirm/block)
- `confirmation` — user yes/no
- `action` — tool result, `verified`, `duration_ms`
- `outcome` — success / partial / blocked

Useful for demo post-mortems and latency analysis without Opik.

---

## Eval dataset

`evals/voice_commands.jsonl` — one JSON object per line:

```json
{"input": "open finder", "expected_tools": ["open_app"], "must_succeed": true}
```

Run: `python -m evals.run` from the `voice/` directory.

---

## Related docs

- [voice/README.md](README.md) — backend setup & env vars
- [../README.md](../README.md) — project overview & demo commands
