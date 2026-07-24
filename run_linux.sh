#!/usr/bin/env bash
# Start the Linux agent with VOICE + anti-hallucination guards.
# - Offline rules planner only (no Ollama inventing steps)
# - Confirm transcript + confirm plan before anything runs
# Use:  ./run_linux.sh --text   for keyboard only.
set -euo pipefail
cd "$(dirname "$0")"

pkill -f "python.*voice_agent.cli" 2>/dev/null || true

if [[ ! -d venv ]]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt

export VOICE_AGENT_PLANNER=rules
export VOICE_AGENT_ALLOW_LLM=0
export VOICE_AGENT_WHISPER_MODEL="${VOICE_AGENT_WHISPER_MODEL:-small.en}"

MODE_ARGS=(--real --planner rules --voice)
if [[ "${1:-}" == "--text" ]]; then
  MODE_ARGS=(--real --planner rules --text)
  echo "=== Voice Agent — TEXT mode (rules planner, confirm plan) ==="
else
  echo "=== Voice Agent — VOICE mode (anti-hallucination) ==="
  echo
  echo "Flow:"
  echo "  1. ENTER → speak → ENTER"
  echo "  2. If [heard] is wrong, TYPE the correct command"
  echo "  3. Read 'I will:' plan → type yes (or the correct command)"
  echo
  echo "Whisper model: $VOICE_AGENT_WHISPER_MODEL (first run may download ~500MB)"
fi
echo

exec python3 -m voice_agent.cli "${MODE_ARGS[@]}"
