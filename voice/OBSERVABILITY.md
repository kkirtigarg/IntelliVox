# Observability & evals (Comet Opik)

IntelliVox tracing uses [Opik](https://www.comet.com/docs/opik/) — open source LLM/agent observability from Comet.

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

> **Port note:** Opik UI uses port `5173`, same as Vite dev server. Don't run `npm run dev` and Opik at the same time, or change Vite port in `intellivox-ui/vite.config.js`.

### Step 2 — Configure IntelliVox SDK

```bash
cd voice
source .venv/bin/activate
pip install opik

cp .env.example .env    # sets OPIK_USE_LOCAL=true

# Or export manually:
export OPIK_USE_LOCAL=true
export OPIK_URL_OVERRIDE=http://localhost:5173/api
export OPIK_WORKSPACE=default

opik configure --use_local --yes
```

### Step 3 — Run IntelliVox backend

```bash
cd voice
source .venv/bin/activate
# load .env if you use direnv, or:
export $(grep -v '^#' .env | xargs)

python -m agent.orchestrator
```

Use a voice command — traces appear in Opik under project **`intellivox`**.

### Step 4 — Verify

```bash
curl http://127.0.0.1:8765/metrics   # opik_tracing: true
```

Stop Opik: `cd ~/opik && ./opik.sh --stop` (see `./opik.sh --help`).

---

## Opik cloud (alternative)

```bash
pip install opik
opik configure              # API key + workspace from comet.com
unset OPIK_USE_LOCAL
python -m agent.orchestrator
```

View traces at [comet.com](https://www.comet.com/).

---

## Disable Opik

```bash
export OPIK_DISABLED=true
```

---

## What gets traced

| Span | Function |
|------|----------|
| `whisper_transcribe` | Speech-to-text |
| `planner_plan` | Ollama task planning |
| `summarize` / `summarize_codebase` | LLM summaries |
| `computer_use` | Vision agent loop |
| `agent_run_task` | Full voice pipeline |

---

## Local metrics (no Opik needed)

```bash
curl http://127.0.0.1:8765/metrics
curl http://127.0.0.1:8765/diagnostics
python scripts/diagnostics.py
python -m evals.run
```

Audit logs: `voice/audit_logs/` (includes `duration_ms` per step).
