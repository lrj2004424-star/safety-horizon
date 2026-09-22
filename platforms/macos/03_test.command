#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python horizon.py test
