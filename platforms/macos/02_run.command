#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONUTF8=1
exec .venv/bin/python horizon.py configure
