# IntelliVox

Voice-controlled desktop agent for **macOS** (primary). Speak natural commands; IntelliVox transcribes with Whisper, plans steps with a local LLM (Ollama), runs tools with safety guardrails, and shows live progress plus summaries in a native UI.

```
Voice → Whisper ASR → Planner → Safety → Tools → WebSocket UI
```

## Repository layout

| Path | Description |
|------|-------------|
| [`voice/`](voice/) | Python backend — ASR, planner, safety, tools, WebSocket server |
| [`intellivox-ui/`](intellivox-ui/) | React + Electron desktop UI (mic, steps, summary panel) |

## Quick start

### 1. Backend

```bash
cd voice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # optional, for web_browse tool

# Requires Ollama + model:
#   brew install ollama && ollama pull llama3.1

python -m agent.orchestrator
# → ws://127.0.0.1:8765/ws
```

### 2. Desktop UI

```bash
cd intellivox-ui
npm install
cp .env.example .env
npm run desktop
```

See [`voice/README.md`](voice/README.md) and [`intellivox-ui/README.md`](intellivox-ui/README.md) for details.

## Build installers

From `intellivox-ui/`:

```bash
npm run desktop:build        # current OS → release/
npm run desktop:build:mac    # .dmg + .zip
npm run desktop:build:win    # NSIS + portable .exe
npm run desktop:build:linux  # AppImage + .deb
```

## What it can do

### Browser & media
- Open Chrome (signed-in profile)
- Google search, YouTube search, **YouTube play** (top result + autoplay)

### Gmail (Chrome)
- Open inbox / search, read messages, **summarize mail** (summary panel)
- Optional save summary to `.txt`

### Documents & PDFs
- Find files (Spotlight + Downloads fallback)
- Read & summarize PDFs
- **Compare two PDFs** with fuzzy name matching + confirm on partial matches
- **Compare mail + PDF** and summarize differences
- Answer questions about document text

### Code
- Summarize a codebase folder; save summary to Desktop

### Desktop
- Open/close apps, screenshots, volume, keyboard/mouse (with confirm where needed)

### Safety
- Rule-based **ALLOW / CONFIRM / BLOCK** (deterministic, no LLM in safety)
- User confirm for destructive or high-risk actions
- Cancel / pause mid-task
- Prompt-injection filtering on read content

## Demo commands

| Say | Result |
|-----|--------|
| *Play Kabira song on YouTube* | Opens & plays top video in Chrome |
| *Open Gmail and summarize my mail* | Inbox summary in UI panel |
| *Compare Shivam and Jaya PDFs in Downloads and summarize* | Comparison panel (confirm if fuzzy match) |
| *Summarize the intellivox-ui codebase* | Bullet code overview |
| *Search Google for weather in Delhi* | Google results in Chrome |

## macOS setup (recommended)

1. **Chrome** — signed into Gmail / YouTube  
2. **Chrome → View → Developer → Allow JavaScript from Apple Events** (Gmail read)  
3. **System Settings → Privacy** — Microphone, Accessibility (clipboard/Gmail fallback), Automation as prompted  
4. Optional: `export INTELLIVOX_CHROME_PROFILE="Default"` if using a non-default Chrome profile  

## Configuration

Backend env vars (`voice/.env` — copy from `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `INTELLIVOX_ENGLISH_ONLY` | `true` | Reject non-English voice input |
| `INTELLIVOX_CHROME_PROFILE` | auto | Chrome profile directory name |
| `INTELLIVOX_VISION_MODEL` | `llama3.2-vision` | Model for `computer_use` |
| `INTELLIVOX_MAX_REPLANS` | `2` | Replan attempts after step failure |
| `OPIK_DISABLED` | — | Set `true` to disable tracing |

UI: `VITE_WS_URL=ws://127.0.0.1:8765/ws` in `intellivox-ui/.env`

## Observability

Tracing via [Comet Opik](voice/OBSERVABILITY.md) (optional). Local metrics:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/metrics
python -m evals.run
```

Audit logs: `voice/audit_logs/`

## Not supported (current)

- YouTube commenting (removed)
- PDF → spreadsheet / slide editing
- Spoken batch file organize (partial)
- Reliable `computer_use` for demos (prefer browser tools)

## License

See repository license file if present.
