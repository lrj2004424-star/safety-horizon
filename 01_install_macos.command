#!/bin/bash
# Guided installer / 引导安装。仅建立项目内环境，不改系统 Python。
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'This release targets Apple Silicon macOS / 本候选版安装器仅支持 Apple Silicon Mac。'; exit 1
fi
if ! command -v uv >/dev/null 2>&1 && [[ ! -x .tools/uv ]]; then
  echo 'Download uv from astral.sh into .tools; then install Python 3.12 and locked dependencies.'
  echo '将从官方源下载 uv/Python/依赖。不安装或授权 TouchDesigner、萤石、Arduino IDE。'
  read -r -p 'Continue? 输入 y 继续: ' answer
  [[ "$answer" == y || "$answer" == Y ]] || exit 1
  installer="$(mktemp -t safety-horizon-uv)"
  trap 'rm -f "$installer"' EXIT
  curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$installer"
  UV_INSTALL_DIR="$PWD/.tools" UV_NO_MODIFY_PATH=1 sh "$installer"
fi
if [[ -x .tools/uv ]]; then UV="$PWD/.tools/uv"; else UV="$(command -v uv)"; fi
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.tools/python"
unset PYTHONPATH PYTHONHOME
if [[ ! -x .venv/bin/python ]]; then "$UV" venv --python 3.12 --seed .venv; fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'
"$UV" pip sync --python .venv/bin/python --require-hashes requirements.lock
.venv/bin/python -m pip check
.venv/bin/python scripts/setup_assets.py --download-models
.venv/bin/python horizon.py doctor
echo 'Done / 安装完成。下一步：bash platforms/macos/02_run.command。仅窗口采集需编译 scripts/build_capture.sh。'
