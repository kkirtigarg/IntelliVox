#!/usr/bin/env bash
# Fix Ollama Vulkan DeviceLost on AMD+NVIDIA (nouveau) hybrid laptops.
# Forces Ollama to use the AMD Radeon Vulkan ICD only.
#
# Usage:
#   bash voice/fix-ollama-gpu.sh
#
set -euo pipefail

ICD="/usr/share/vulkan/icd.d/radeon_icd.x86_64.json"
DROPIN_DIR="/etc/systemd/system/ollama.service.d"
DROPIN="$DROPIN_DIR/amd-vulkan.conf"

if [[ ! -f "$ICD" ]]; then
  echo "ERROR: AMD Vulkan ICD not found at $ICD"
  exit 1
fi

echo "Writing systemd drop-in to force AMD Vulkan for Ollama…"
sudo mkdir -p "$DROPIN_DIR"
sudo tee "$DROPIN" >/dev/null <<EOF
[Service]
# Avoid nouveau Vulkan (causes vk::DeviceLostError on RTX 30-series)
Environment=VK_ICD_FILENAMES=$ICD
# Optional: uncomment next line for CPU-only if AMD still crashes
# Environment=OLLAMA_NUM_GPU=0
EOF

echo "Reloading and restarting ollama…"
sudo systemctl daemon-reload
sudo systemctl restart ollama
sleep 2
sudo systemctl --no-pager --full status ollama | head -20

echo
echo "Quick test:"
curl -sS --max-time 60 http://127.0.0.1:11434/api/chat \
  -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"Return JSON {\"ok\":true}"}],"stream":false,"format":"json","options":{"num_predict":16}}' \
  | head -c 400
echo
echo
echo "Done. Re-try voice command: Open notepad app"
