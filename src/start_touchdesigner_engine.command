#!/bin/bash
# TD and standalone share one lifecycle / TD 与独立版共用后端。
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH PYTHONHOME
[[ -x .venv/bin/python ]] || { echo 'Run release installer first'; exit 1; }
exec .venv/bin/python horizon.py run --no-browser --no-web --parent-pid "$PPID"
