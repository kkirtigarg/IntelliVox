# IntelliVox Desktop

Cross-platform voice agent **desktop app** for **macOS**, **Windows**, and **Linux**.

Built with React + Electron. This package is the **UI only** — it connects to your agent backend over WebSocket. Whisper, speech-to-text, and task execution run in a separate backend (e.g. the `voice/` folder in this repo).

## Quick start (desktop)

```bash
npm install
cp .env.example .env          # set backend WebSocket URL
npm run desktop               # native window + hot reload
```

Start your agent backend separately before using the mic:

```bash
# example — from repo root
cd ../voice && .venv/bin/python -m agent.orchestrator
```

## Build installers

Build for your current OS:

```bash
npm run desktop:build
```

Platform-specific:

```bash
npm run desktop:build:mac      # .dmg + .zip
npm run desktop:build:win      # NSIS installer + portable .exe
npm run desktop:build:linux    # AppImage + .deb
```

Installers are written to `release/`.

## Configuration

Set the backend WebSocket URL in `.env` before building or running:

```
VITE_WS_URL=ws://127.0.0.1:8765/ws
```

This is baked into the app at build time. Change `.env` and rebuild to point at a different server.

## Browser mode (optional)

You can still run the UI in a browser for development:

```bash
npm run dev    # http://localhost:5173
```

## What's included

| Included | Not included |
|----------|--------------|
| Native desktop window (Electron) | Whisper / ASR |
| React UI (mic, waveform, steps) | Task planner |
| WebSocket client | Desktop automation tools |
| macOS / Windows / Linux installers | Python backend |

## Project layout

```
intellivox-ui/
├── electron/    # Desktop shell (main + preload)
├── src/         # React UI
├── public/      # Static assets
├── dist/        # Web build (used by packaged app)
└── release/     # Installers (after desktop:build)
```
