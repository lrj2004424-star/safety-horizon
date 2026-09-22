# 01 Installation / 按系统安装

| 系统 | 下载包 | 详细步骤 |
|---|---|---|
| macOS Apple Silicon | `macOS-arm64.zip` | [macOS](../platforms/macos/README.md) |
| Windows 10/11 x64 | `Windows-x64.zip` | [Windows](../platforms/windows/README.md) |

解压完整包，在根目录运行，不要搬走单个脚本。Python 3.12、OpenCV、MediaPipe、NumPy、pySerial 安装到项目 `.venv`；Windows 另装 pywin32、mss；分别使用带哈希锁文件。

首次联网下载模型并核验 SHA-256。窗口采集需要萤石客户端；RTSP/本地视频不需要。TD 可选，PyCharm/cvzone 非必需；Arduino 工具仅烧录板卡时需要。

安装器不修改系统 Python、不代购许可、不代填账号、不关闭安全保护。`.venv` 不能跨系统或随目录搬运；移动源码后重新安装。不要向重要现有环境执行 uv pip sync，只对项目隔离环境使用。
