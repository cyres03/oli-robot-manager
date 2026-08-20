#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION="$(tr -d '[:space:]' < VERSION)"
case "$(uname -m)" in
  x86_64) ARCH="x86_64" ;;
  aarch64 | arm64) ARCH="arm64" ;;
  *) ARCH="$(uname -m)" ;;
esac
DIST_DIR="$ROOT_DIR/dist/linux"
BUILD_DIR="$ROOT_DIR/build/linux"
RELEASE_DIR="$ROOT_DIR/release/linux"
PACKAGE_NAME="OliRobotManager-Linux-${ARCH}-v${VERSION}"

if [[ ! -f resources/backlash/backlash_install.zip ]]; then
  echo "Missing resources/backlash/backlash_install.zip." >&2
  echo "Run ./scripts/linux_setup_backlash_resource.sh before building." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  bash scripts/linux_setup.sh
fi

source .venv/bin/activate
python -m pip install --upgrade pyinstaller
python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  OliRobotManager-linux.spec

mkdir -p "$RELEASE_DIR"
tar -C "$DIST_DIR" -czf "$RELEASE_DIR/$PACKAGE_NAME.tar.gz" OliRobotManager

echo "Linux application: dist/linux/OliRobotManager/OliRobotManager"
echo "Linux release: release/linux/$PACKAGE_NAME.tar.gz"