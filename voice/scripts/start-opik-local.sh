#!/usr/bin/env bash
# Start local Opik (Docker) for IntelliVox tracing.
# Docs: https://www.comet.com/docs/opik/self-host/local_deployment
set -e

OPIK_DIR="${OPIK_DIR:-$HOME/opik}"

if [ ! -d "$OPIK_DIR" ]; then
  echo "Cloning Opik into $OPIK_DIR ..."
  git clone https://github.com/comet-ml/opik.git "$OPIK_DIR"
fi

echo "Starting Opik (Docker) ..."
cd "$OPIK_DIR"
./opik.sh

echo ""
echo "Opik UI:  http://localhost:5173"
echo "Configure IntelliVox SDK:"
echo "  cd voice"
echo "  export OPIK_USE_LOCAL=true"
echo "  export OPIK_URL_OVERRIDE=http://localhost:5173/api"
echo "  opik configure --use_local --yes"
echo ""
echo "Then restart: python -m agent.orchestrator"
