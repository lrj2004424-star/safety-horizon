# Safety Horizon · 安全视界

**视频证据 → 风险分析 → 安全员审核 → 提醒与追溯**

由 **Safety Horizon--Lrj** 设计的裁床作业视觉风险辅助监测系统。集成单工位姿态分析、事件录像、逐帧标注、1–5 级人工评定、审核后蜂鸣器提醒与阈值建议。独立浏览器界面运行，**不要求安装 TouchDesigner**。

**v0.2.0-rc.1 · MIT · Pre-release**

## 1. 按系统下载 / Choose your platform

在 [Releases](https://github.com/lrj2004424-star/safety-horizon/releases) 选择一个包：

| 电脑 | 文件后缀 | 使用说明 |
|---|---|---|
| macOS · Apple Silicon（M 系列） | `macOS-arm64.zip` | [macOS 说明](platforms/macos/README.md) |
| Windows 10/11 · x64 | `Windows-x64.zip` | [Windows 说明](platforms/windows/README.md) |

解压**完整包**，先读 `00_START_HERE.md`。两包共享核心代码，各有平台入口、依赖锁和采集方式。Intel Mac、Windows ARM、Linux 不在本版验证范围。

## 2. 安装 / Install

首次联网下载 Python 3.12、OpenCV、MediaPipe 等依赖及模型；不需要 PyCharm/cvzone。萤石客户端和账号由用户自行安装、登录。

### macOS

在解压根目录打开终端：

```bash
bash platforms/macos/01_install.command
# 仅窗口采集需要 Apple 编译工具和录屏权限：
xcode-select --install
bash scripts/build_capture.sh
bash platforms/macos/02_run.command
```

已有编译工具可跳过 xcode-select；RTSP/本地视频不需要窗口助手。

### Windows

在解压根目录打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File platforms\windows\01_install.ps1
```

安装后双击 `platforms\windows\02_run.cmd`。脚本只建立项目 `.venv`，不替换系统 Python。公司策略阻止时走正规授权流程，不关闭安全保护。

### 已有 Python 3.12

| 操作 | macOS | Windows |
|---|---|---|
| 创建环境 | `python3.12 -m venv .venv` | `py -3.12 -m venv .venv` |
| Python 路径（P） | `.venv/bin/python` | `.venv\Scripts\python.exe` |
| 锁文件（L） | `requirements.lock` | `requirements-windows.lock` |

用对应实际路径替换 P、L：

```text
P -m pip install --require-hashes -r L
P -m pip check
P scripts/setup_assets.py --download-models
P horizon.py doctor
P horizon.py configure
```

## 3. 输入与审核 / Run

启动后选择 **1 窗口、2 RTSP、3 本地视频、4 仅审核界面**。

- **窗口**：先播放萤石直播。Windows 选择窗口和视频区域，必须保持无遮挡；macOS 沿用原生窗口采集。
- **RTSP**：输入自己有权访问的摄像头地址；需设备支持。密码输入隐藏，私有配置保存在不入库的 `src/runtime`。
- **本地视频**：放入 `src/test_videos` 后输入文件路径；测试单独统计，不驱动真实提醒。
- **审核**：选择事件 → 回放/逐帧 → 标注 → 1–5 级评定 → 提交，以服务回执为准。

更换来源/机位后须重新标定工位范围，示例配置不自动适用新现场。退出在启动终端按 **Ctrl+C**；关闭浏览器不等于退出后端。

## 4. 提醒与安全边界

默认静音。实际提醒需显式启用，例如：

```text
P horizon.py run --source-config src/runtime/source.private.json --alerts computer
P horizon.py run --source-config src/runtime/source.private.json --alerts uno
```

macOS 默认窗口输入可省略 source-config。UNO 先烧录 `src/hardware/arduino_status_indicator/arduino_status_indicator.ino`。有效实时事件经人工 3–5 级审核，才授权一次两短一长；测试、未审核及 1–2 级不响。

作者确认完成其现场及真实硬件验收测试，报告准确率 **91%–100%**；不是所有机位、平台及本次 Windows 适配的准确率保证。人工复核不替代机器联锁与安全防护。见 [测试说明](docs/10_PROJECT_STATEMENT.md)、[验证记录](docs/08_VALIDATION.md)。

## 5. 文件导航

| 目录 | 用途 |
|---|---|
| `platforms/macos` / `platforms/windows` | 两平台安装、启动、测试和说明 |
| `src` | 共用视觉、审核、存储、硬件及 TD 源码 |
| `modules/01_capture` 至 `07_touchdesigner` | 七步工作流说明，不必重复下载七份代码 |
| `docs` | 接口、隐私、验收和发布记录 |
| `FILE_INDEX.md` / `SHA256SUMS` | 文件用途与校验清单 |

可选 TD 节点图见 [TD 说明](docs/04_TOUCHDESIGNER.md)。Windows 本版使用浏览器，不宣称 Windows TD 工程已适配。原用户 TD 工程不会被替换。

## 6. 常见问题

**黑屏？** 检查实时播放、遮挡/最小化、裁切范围、权限或 RTSP 连通性。无可靠输入时保持不确定，不伪造骨架。

**为何 Python 3.12？** 两平台依赖按此版本锁定，避免与其他环境冲突。

**怎样测试？** 运行对应平台的 `03_test` 文件；不操作真实硬件。自动测试见 [Actions](https://github.com/lrj2004424-star/safety-horizon/actions)。

**什么不能上传？** `src/runtime`、`src/annotations`、现场录像、模型、账号配置和 `.venv`。发布使用白名单打包脚本。

## 7. 作者与许可

**Safety Horizon--Lrj** · Copyright © 2026 · [MIT](LICENSE) · [问题反馈](https://github.com/lrj2004424-star/safety-horizon/issues)。
