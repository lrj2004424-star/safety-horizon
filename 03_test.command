#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
unset PYTHONPATH PYTHONHOME
exec .venv/bin/python horizon.py test
