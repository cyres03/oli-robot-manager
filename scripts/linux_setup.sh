#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cat <<'EOF'
Linux development environment is ready.

Run:
  source .venv/bin/activate
  python main.py

If PyQt6 fails to start on Ubuntu/Debian, install system packages:
  sudo apt update
  sudo apt install -y libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 network-manager openssh-client
EOF