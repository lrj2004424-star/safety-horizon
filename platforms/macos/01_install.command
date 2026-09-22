#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec bash 01_install_macos.command
