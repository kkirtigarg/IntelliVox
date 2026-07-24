#!/usr/bin/env bash
# Reset the participant evaluation desktop (sample files + task state).
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source venv/bin/activate 2>/dev/null || true
python3 -m voice_agent.cli --reset-env "$@"
echo
echo "Workspace restored under: environment/workspace/"
echo "Documents include: invoice.pdf, budget.csv, meeting_notes.txt, …"
