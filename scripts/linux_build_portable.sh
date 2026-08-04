#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d .venv ]]; then
  bash scripts/linux_setup.sh
fi

source .venv/bin/activate
python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean OliRobotManager-linux.spec

echo "Portable Linux build completed: dist/OliRobotManager"