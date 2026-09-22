#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
unset PYTHONPATH PYTHONHOME
[[ -x .venv/bin/python ]] || { echo 'Run 01_install_macos.command first / 请先安装'; exit 1; }
exec .venv/bin/python horizon.py run "$@"
