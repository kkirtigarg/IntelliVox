# IntelliVox Voice Backend

Python agent server: **Whisper ASR**, **Ollama planner**, **safety guardrails**, and **desktop/browser tools** over WebSocket.

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| Python 3.11+ | 3.13 tested |
| [ffmpeg](https://ffmpeg.org/) | `brew install ffmpeg` — WebM audio from UI |
| [Ollama](https://ollama.com/) | `ollama pull llama3.1` for planning & summaries |
| Google Chrome | Gmail / YouTube automation uses your signed-in profile |
| yt-dlp | Included in `requirements.txt` — YouTube play lookup |

Optional:
- `playwright install chromium` — for `web_browse` tool
- `ollama pull llava:7b` — if using `computer_use` (`INTELLIVOX_VISION_MODEL=llava:7b`)

## Setup

```bash
cd voice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m agent.orchestrator
```

Server listens on **`ws://127.0.0.1:8765/ws`**.

HTTP helpers:
- `GET /health` — liveness
- `GET /metrics` — run stats from audit logs
- `GET /diagnostics` — environment checklist

## Architecture

```
agent/
├── orchestrator.py   # FastAPI + WebSocket + Whisper + task loop
├── planner.py        # Deterministic routers + Ollama fallback
├── safety.py         # ALLOW / CONFIRM / BLOCK rules
├── verifier.py       # Post-step checks
├── audit.py          # JSONL session logs
├── tools/            # Tool implementations
│   ├── browser.py    # Chrome, Google, YouTube
│   ├── gmail.py      # Gmail read (JS + clipboard fallback)
│   ├── files.py      # find_file, find_compare_pdf_pair
│   ├── document.py   # PDF, summarize, compare_summarize
│   └── ...
└── telemetry.py      # Opik tracing (optional)
```

## Deterministic planners

These routes bypass the LLM when phrasing matches:

| Router | Example voice command |
|--------|------------------------|
| YouTube | *Search / play X on YouTube* |
| Gmail | *Open Gmail and summarize my mail* |
| Compare PDFs | *Compare A and B PDFs in Downloads and summarize* |
| Mail + PDF | *Compare my mail with report.pdf and summarize* |
| Codebase | *Summarize the voice codebase* |

## Tools registry

Registered in `agent/tools/__init__.py`:

**Browser:** `open_browser`, `navigate_url`, `google_search`, `youtube_search`, `youtube_play`

**Gmail:** `open_gmail`, `read_gmail`

**Files:** `find_file`, `find_compare_pdf_pair`, `list_files`, `read_file`, `write_file`, `delete_file`, `move_file`, `open_file`

**Documents:** `read_pdf`, `summarize`, `compare_summarize`, `summarize_codebase`, `save_summary_file`, `answer_question`

**Desktop:** `open_app`, `close_app`, `type_text`, `press_key`, `click`, `take_screenshot`, `set_volume`

**Advanced:** `web_browse` (Playwright), `computer_use` (vision + pyautogui — confirm required)

## macOS permissions

System Settings → **Privacy & Security**:

- **Microphone** — voice input
- **Accessibility** — keyboard/clipboard fallbacks (Gmail read without Chrome JS)
- **Automation** — AppleScript control of Chrome / apps

Chrome: **View → Developer → Allow JavaScript from Apple Events** for Gmail inbox read.

## Environment variables

Copy `.env.example` to `.env`:

```bash
# Voice / agent
INTELLIVOX_ENGLISH_ONLY=true          # default: English-only commands
INTELLIVOX_NON_ENGLISH_REJECT_PROB=0.35
INTELLIVOX_MAX_REPLANS=2
INTELLIVOX_CHROME_PROFILE=Default     # optional Chrome profile override
INTELLIVOX_VISION_MODEL=llama3.2-vision

# Opik tracing (optional — see OBSERVABILITY.md)
OPIK_DISABLED=true
# OPIK_USE_LOCAL=true
# OPIK_URL_OVERRIDE=http://localhost:5173/api
```

## Evals & diagnostics

```bash
python -m evals.run                    # planner routing checks
python scripts/diagnostics.py          # env + dependency report
```

Sample eval cases: `evals/voice_commands.jsonl`

## Observability

See **[OBSERVABILITY.md](OBSERVABILITY.md)** for Comet Opik setup (local Docker or cloud).

Session audit logs (plans, safety, actions, timings): `audit_logs/<session_id>.jsonl`
