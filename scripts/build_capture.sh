#!/bin/bash
# Compile project-owned capture helpers / 在本机编译采集器，不分发旧二进制。
set -euo pipefail
cd "$(dirname "$0")/../src/macos_window_capture"
if ! xcrun --find clang >/dev/null 2>&1 || ! xcrun --find swiftc >/dev/null 2>&1; then
  echo 'Install Apple Command Line Tools: xcode-select --install; then rerun. / 请完成苹果开发工具安装后重试。'; exit 1
fi
export CLANG_MODULE_CACHE_PATH="$PWD/../../.cache/clang"
export SWIFT_MODULECACHE_PATH="$PWD/../../.cache/swift"
mkdir -p "$CLANG_MODULE_CACHE_PATH" "$SWIFT_MODULECACHE_PATH"
xcrun clang -O2 EZVIZWindowCaptureCG.c -framework ApplicationServices -framework CoreFoundation -o ezviz_window_capture_cg
xcrun clang -O2 ezviz_window_list.c -framework ApplicationServices -framework CoreFoundation -o ezviz_window_list_c
xcrun swiftc -parse-as-library -O EZVIZWindowCapture.swift -o ezviz_window_capture
echo 'Capture helpers built / 采集器已生成。首次运行需允许屏幕录制权限。'
