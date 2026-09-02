#!/usr/bin/env bash
set -euo pipefail

# 이 속성의 사이클 진입점. 엔진은 어느 속성이 있는지 알지 않는다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/../../engine/scripts/run_catalog_cycle.py" \
  --profile "$SCRIPT_DIR/profile.json" \
  "$@"
