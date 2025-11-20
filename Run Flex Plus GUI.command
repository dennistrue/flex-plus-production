#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v git >/dev/null 2>&1 && [ -d "${SCRIPT_DIR}/.git" ]; then
  before="$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo "")"
  git -C "${SCRIPT_DIR}" fetch --quiet --tags || true
  git -C "${SCRIPT_DIR}" pull --ff-only || true
  after="$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo "")"
  if [[ -n "${before}" && -n "${after}" && "${before}" != "${after}" ]]; then
    exec "$0"
  fi
fi
cd "${SCRIPT_DIR}/bin"

PYTHON_BIN="${FLEX_PYTHON:-python3}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  osascript -e 'display alert "Flex Plus Flash GUI" message "python3 was not found on this Mac. Install Python 3 and try again."'
  exit 1
fi

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/bin/flash_gui.py"
