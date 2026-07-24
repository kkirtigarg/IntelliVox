# Voice-Controlled Computer Use Agent — Prototype

See [`DESIGN.md`](DESIGN.md) for the full architecture and rationale.

**No API key is required.** Planning defaults to an offline rule-based planner,
with optional local Ollama if you want open-ended language understanding.

## What's runnable right now (anywhere)

Demo mode uses a `MockActuator` and text-mode voice (type instead of speak):

```bash
pip install pyyaml
python3 -m voice_agent.cli --demo
```

Try:
```
open notepad
list windows
screenshot
delete the file at C:/report.docx      # asks for confirmation
write "hello" to C:/tmp/note.txt
go to https://wikipedia.org
pause
resume
cancel
```

Tests:

```bash
pip install pyyaml pytest
python3 -m pytest tests/ -v
```

## Real usage on a Windows machine (no API key)

### 1. Install

In PowerShell:

```powershell
cd path\to\voice_agent_project
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Optional browser backend:
playwright install chromium
```

Optional local LLM (still no cloud key):

```powershell
# Install Ollama from https://ollama.com then:
ollama pull llama3.2:1b
```

Optional OCR for `read screen` / `read screen text`:

- Install [Tesseract for Windows](https://github.com/UB-Mannheim/tesseract/wiki)
- Ensure `tesseract.exe` is on PATH

### 2. Run

```powershell
# Text input + real Windows GUI (recommended first)
python -m voice_agent.cli --real --text --planner rules

# Mic + speakers when faster-whisper/sounddevice/pyttsx3 work
python -m voice_agent.cli --real --planner auto

# Rules first, then local Ollama for unrecognized phrasing
python -m voice_agent.cli --real --text --planner ollama

# Also attach Playwright for reliable browser DOM actions
python -m voice_agent.cli --real --text --with-browser
```

What `--real` uses on Windows:
- `WindowsGuiActuator` — open apps, focus windows, type/click, screenshots, real files
- `LocalWhisperVoicePipeline` when audio deps work; otherwise automatic text fallback (`--text` forces it)
- `CompositeActuator` + Playwright when `--with-browser` is set
- Planner: **rules (default) → Ollama if running → Anthropic only if you set a key**

### 3. Example Windows commands

```
open notepad
list windows
focus notepad
type "hello from the voice agent"
screenshot
write "meeting notes" to C:\Users\Public\agent_note.txt
read the file C:\Users\Public\agent_note.txt
delete the file at C:\Users\Public\agent_note.txt
go to https://en.wikipedia.org
```

## Real usage on Linux (no API key)

### 1. Install

```bash
cd path/to/voice_agent_project
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Optional browser backend:
playwright install chromium
# Recommended for window list/focus (pick one):
sudo pacman -S wmctrl    # Manjaro/Arch
# sudo apt install wmctrl   # Debian/Ubuntu
```

Optional OCR: install `tesseract` from your package manager and ensure `tesseract` is on PATH.

Optional local LLM: install [Ollama](https://ollama.com), then `ollama pull llama3.2:1b`.

### 2. Run

```bash
# Quick start script
./run_linux.sh

# Or manually — text input + real Linux GUI (recommended first)
python3 -m voice_agent.cli --real --text --planner rules

# Mic + speakers when faster-whisper/sounddevice/pyttsx3 work
python3 -m voice_agent.cli --real --planner auto

# Also attach Playwright for reliable browser DOM actions
python3 -m voice_agent.cli --real --text --with-browser
```

What `--real` uses on Linux:
- `LinuxGuiActuator` — open apps via PATH (`gedit`, `firefox`, `nautilus`, …), wmctrl/xdotool for windows, pyautogui for click/type/screenshot, `xdg-open` for files
- Same voice pipeline and planner behavior as Windows

### 3. Example Linux commands

```
open gedit
open notepad          # maps to gedit/xed/kate when available
list windows
focus firefox
type "hello from the voice agent"
screenshot
write "meeting notes" to /tmp/agent_note.txt
read the file /tmp/agent_note.txt
delete the file at /tmp/agent_note.txt
go to https://en.wikipedia.org
```

Consequential actions (delete, send message, shutdown, …) always ask for an explicit yes/no.

## Participant evaluation desktop

The agent ships a preconfigured desktop workspace with common apps and sample files:

| Role | Voice/text name | App on this Linux machine |
|---|---|---|
| Web browser | `browser` / `firefox` | Firefox |
| File manager | `files` / `explorer` | Thunar |
| PDF viewer | `pdf` / `pdf viewer` | Evince |
| Text editor | `notepad` / `text editor` | Mousepad |
| Spreadsheet | `spreadsheet` / `calc` / `excel` | LibreOffice Calc |
| Document editor | `writer` / `word` | LibreOffice Writer |
| Presentation | `impress` / `powerpoint` | LibreOffice Impress |

Sample files (resettable):

```
environment/workspace/Documents/
  invoice.pdf
  budget.csv
  meeting_notes.txt
  draft_letter.txt
  friday_deck_outline.txt
```

```bash
# Restore sample files between tasks
python3 -m voice_agent.cli --reset-env
./reset_environment.sh

# List / load a predefined task
python3 -m voice_agent.cli --list-tasks
python3 -m voice_agent.cli --real --text --task task_open_invoice

# Or say/type during a session:
reset environment
```


| Value | Needs | Behavior |
|---|---|---|
| `auto` (default) | nothing | Rules first; Ollama if local daemon is up; Anthropic only if `ANTHROPIC_API_KEY` is set |
| `rules` | nothing | Fully offline keyword/pattern planner |
| `ollama` | Ollama + model | Rules first, then local LLM |
| `anthropic` | `ANTHROPIC_API_KEY` | Cloud planner if configured, else rules |

## Project layout

```
voice_agent_project/
├── README.md                 # How to run (this file)
├── DESIGN.md                 # Architecture & safety rationale
├── requirements.txt          # Python dependencies
├── run_linux.sh              # Linux quick-start (voice/text + real GUI)
├── run_windows.bat           # Windows quick-start
├── reset_environment.sh      # Restore sample desktop files
│
├── voice_agent/              # Application package (import: voice_agent.*)
│   ├── cli.py                # Entry: python -m voice_agent.cli
│   ├── orchestrator.py       # Plan → policy → confirm → act → verify
│   ├── planner.py            # Rules (+ optional Ollama / Anthropic)
│   ├── session_memory.py     # Multi-turn memory (e.g. "open first link")
│   ├── policy_engine.py      # Permission checks
│   ├── policy_rules.yaml     # Allow/deny / confirm rules
│   ├── control_detector.py   # pause / resume / cancel / correction
│   ├── state_machine.py      # Task lifecycle
│   ├── verification.py       # Post-action checks
│   ├── voice_pipeline.py     # Mic (Whisper) or text input
│   ├── audit.py              # Audit log writer
│   ├── models.py             # Shared data types
│   ├── eval_env.py           # Participant tasks + workspace reset
│   └── actuators/            # How actions touch the machine
│       ├── base.py           # Interface
│       ├── mock.py           # Demo / tests (no real GUI)
│       ├── desktop.py        # Picks Windows vs Linux
│       ├── windows_gui.py
│       ├── linux_gui.py
│       ├── browser.py        # Optional Playwright
│       └── composite.py      # Desktop + browser together
│
├── tests/                    # pytest suite (offline)
│
└── environment/              # Evaluation desktop fixtures
    ├── tasks.yaml            # Predefined participant tasks
    ├── golden/               # Clean sample files (committed)
    └── workspace/            # Live copy (gitignored; restored from golden)
```

**Not committed** (local only): `venv/`, `state/`, `audit_log.jsonl`, `__pycache__/`, `environment/workspace/`.

See [`DESIGN.md`](DESIGN.md) for how these pieces talk to each other.

## Safety properties (covered by tests)

- Fail closed for unknown action categories
- Deny rules cannot be overridden by confirmation
- Deterministic `PolicyEngine.evaluate()`
- Structural injection resistance for ingested content
- Deterministic pause/resume/cancel/correction
- Post-condition verification + masked audit trail
