#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${BACKLASH_RELEASE_TAG:-backlash-resource-v1}"
REPOSITORY="${BACKLASH_REPOSITORY:-cyres03/oli-robot-manager}"
RESOURCE_DIR="$ROOT_DIR/resources/backlash"
TARGET_PATH="$RESOURCE_DIR/backlash_install.zip"
EXPECTED_SHA256="BACC27196221226AFE5339F3A47C9E492C565327DA0E454A7B223370E32A58EE"

verify_resource() {
  local actual_sha256
  actual_sha256="$(sha256sum "$TARGET_PATH" | awk '{print toupper($1)}')"
  if [[ "$actual_sha256" != "$EXPECTED_SHA256" ]]; then
    echo "Backlash resource checksum mismatch." >&2
    echo "Expected: $EXPECTED_SHA256" >&2
    echo "Actual:   $actual_sha256" >&2
    return 1
  fi
}

if [[ -f "$TARGET_PATH" ]]; then
  verify_resource
  echo "Backlash resource is ready: $TARGET_PATH"
  exit 0
fi

mkdir -p "$RESOURCE_DIR"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required for the private Backlash release." >&2
  echo "Install gh and authenticate before running this script." >&2
  exit 1
fi

gh release download "$TAG" \
  --repo "$REPOSITORY" \
  --pattern "backlash_install.zip" \
  --dir "$RESOURCE_DIR" \
  --clobber

if ! verify_resource; then
  rm -f "$TARGET_PATH"
  exit 1
fi

echo "Backlash resource downloaded and verified: $TARGET_PATH"
