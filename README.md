# Safety Horizon · 安全视界

**Visible evidence. Human review. Traceable decisions.**

由 **Safety Horizon--Lrj** 设计的单工位视觉风险观察与人工审核系统。以“可见的证据、可解释的判断、可追溯的处置”为设计主线，串联视频采集、OpenCV / MediaPipe 姿态分析、风险建议、事件录像、人工标注和审核后提醒。

**Version: v0.1.0-rc.1 · Pre-release / 预发布候选版 · Author: Safety Horizon--Lrj · MIT**

项目作者 **Safety Horizon--Lrj** 确认已完成现场及真实硬件验收测试，并报告其测试中的准确率为 **91%–100%**。系统结合视觉分析与安全员人工复核，提供风险辅助监测、预警及事件追溯。该区间属于作者现场测试报告，不是本发布包自动化测试计算出的指标，也不代表所有场景或平台的性能保证。详见 [项目与测试说明](docs/10_PROJECT_STATEMENT.md)。

> 用于辅助风险观察，不替代机器联锁、现场安全制度或专业安全评估；不用于受伤诊断。作者现场验收与工业安全认证、新平台适配是不同事项。有骨架不等于危险判断准确，人工审核也不保证消除漏报。

## 01 / Features · 功能亮点

- **一套核心 / Shared engine**：独立浏览器审核台与可选 TouchDesigner 共用视觉、审核、存储和授权规则。
- **证据链 / Evidence**：采集 → 单工位姿态与风险 → 前 3 秒 / 后 5 秒缓存 → 安全员 1–5 级评定 → 存储 / 审核后提醒 → 阈值建议。
- **人工把关 / Human-in-the-loop**：原始视觉高分不直接触发蜂鸣器；测试样本不得驱动真实输出。
- **可追溯 / Traceable**：逐帧回放、手部区域、行为、遮挡和帧标注；JSON 为原始记录，CSV / JSONL 为导出。
- **故障可见 / Fail-visible**：过期画面隐藏、无可靠姿态保持不确定、旧命令不重放、审核服务退出有限重启。
- **本地处理 / Local-first**：浏览器只监听 127.0.0.1，本项目不向云端上传视频。摄像头客户端自己的云服务不在本项目控制范围。

## 02 / Choose your route · 选择方式

| 用户情况 | 入口 | 需要 TD？ |
|---|---|---|
| 没有 Python / OpenCV | `01_install_macos.command` 引导安装，再 `02_run_standalone.command` | 否 |
| 已有 Python 3.12 | 下方 Route B，安装独立依赖后运行 | 否 |
| 已有匹配依赖 | `python horizon.py doctor` 后 `python horizon.py run` | 否 |
| 想看节点连接 | `docs/04_TOUCHDESIGNER.md`，在新 TD 工程生成网络 | 可选 |
| 只研究其中一步 | `modules/01_capture` 至 `modules/07_touchdesigner` | 可选 |

OpenCV、MediaPipe 是 Python 库，不是必须单独打开的应用。PyCharm、cvzone 不必安装；Arduino 工具只在烧录 UNO 时需要。模块共享 `src/`，避免多份代码不同步。

## 03 / Requirements · 环境要求

| 项目 | 本候选版范围 |
|---|---|
| 系统 / CPU | 当前可安装运行路径为 Apple Silicon macOS；Windows 适配尚未完成，不能按本包命令直接运行；Linux/Intel Mac 不在本次验证范围 |
| Python | **3.12.x**，不把图标上的 3.14 当作已支持版本 |
| 依赖 | `requirements.lock` 固定版本及 SHA-256，避免同时安装多个提供 cv2 的包 |
| 输入 | 萤石客户端的实时播放器，或自己有权使用的本地测试视频 |
| 窗口采集 | Apple Command Line Tools + 项目采集助手 + 用户录屏授权 |
| 硬盘 | 建议至少 10 GiB 余量并制定录像保留周期 |
| 网络 | 首次下载 Python / PyPI 依赖 / Google 模型需要网络 |
| TouchDesigner | 可选；原项目开发版本 2025.33070，新构建工程仍需 TD 实测 |

安装脚本不覆盖系统 Python，不卸载 3.14。TouchDesigner、萤石和 Arduino IDE 由用户从官方安装并授权，不能被本项目“静默一键授权”。

## 04 / Install · 安装

解压到本机可写目录，避免在压缩包内或同步未完成的云盘运行。打开终端并 `cd` 到解压目录。

### Route A — Guided / 无环境用户

```bash
bash 01_install_macos.command
```

缺少 uv 时，经确认从官方源安装到项目 `.tools`；建立 `.venv`，安装锁定依赖并校验模型。不会改变系统 Python。不要关闭系统安全保护；Finder 阻止脚本时可使用上述终端命令及系统正规授权流程。

### Route B — Existing Python / 已有 Python 3.12

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip check
.venv/bin/python scripts/setup_assets.py --download-models
.venv/bin/python horizon.py doctor
```

已有完全匹配的隔离环境可用其 Python 替代 `.venv/bin/python`；TD 启动器默认读取发布根目录的 `.venv`。

### Live capture helper / 实时采集助手

```bash
xcode-select --install
# 已有 Apple Command Line Tools 则跳过上一行；等待安装完成后：
bash scripts/build_capture.sh
```

本地测试视频不需要窗口采集器。首次实时采集需手动允许屏幕录制权限。

## 05 / Run · 运行顺序

1. 回归：`.venv/bin/python horizon.py test`，不启动真实报警硬件。
2. 只看界面：`.venv/bin/python horizon.py run --no-vision`，无视频就显示等待，不伪造事件。
3. 实时检测：登录萤石，确认“实时视频”而非“录像”，完成权限和单工位标定，然后：

```bash
.venv/bin/python horizon.py run
```

4. 浏览器会打开；若没打开，用终端打印的完整本地地址，包含临时访问标记，不要分享。
5. 选择队列事件 → 回放 / 逐帧 → 标注 → 评定等级 → 提交。以服务回执为准，不能把点击当作保存成功。
6. 退出：在启动终端按 `Ctrl+C`。关闭浏览器标签页不等于停止后端。

### Test input / 无需 TD 或萤石的本地测试

放入合法素材 `src/test_videos/sample.mp4`：

```bash
.venv/bin/python horizon.py run --test-video src/test_videos/sample.mp4
```

测试保持静音，独立统计。包中不附工厂视频，不把模拟视频当作真实准确率证据。

### Reviewed alerts / 显式开启人工审核后的提醒

默认 `--alerts none`。现场检查后可二选一：

```bash
.venv/bin/python horizon.py run --alerts computer
.venv/bin/python horizon.py run --alerts uno
```

仍需实时有效、非模拟样本、人工 3–5 级审核，才授权一次两短一长。UNO 先烧录 `src/hardware/arduino_status_indicator/arduino_status_indicator.ino`；真实硬件必须另外验收。

## 06 / Files & workflow · 文件导航

```text
README.md                          从这里开始 / Start here
01_install_macos.command            安装 / Install
02_run_standalone.command           独立运行 / Run
03_test.command                    回归 / Test
horizon.py                         统一启动与退出
web_portal.py + ui/                 本地浏览器审核界面
modules/01_capture … 07_touchdesigner/  按步骤的程序入口说明
src/                               共享核心模块、固件、TD 构建源码及测试
docs/01 … 10_*.md                   编号说明、接口、验收、发布及项目介绍
requirements.lock                  依赖版本和哈希
FILE_INDEX.md                      每个发布文件的用途索引
SHA256SUMS                         发布文件完整性清单
```

运行生成的 `src/annotations`、`src/runtime`、`src/artifacts` 和用户测试视频不进入 Git。不要将运行后的目录直接重新压缩公开；使用白名单打包脚本。

## 07 / FAQ · 常见问题

**必须装 TD？** 不必。浏览器版复用同一后端，TD 是可选的节点可视化。

**已有 Python 3.14 为什么安装 3.12？** 本次发布只验证 3.12，不假定原生视觉依赖兼容其他版本。

**黑屏/无骨架？** 查实时输入、权限、播放器可见性、采集助手和 `src/runtime/vision.log`。遮挡或低置信时应保持不确定，不能绘制假骨架。

**为什么没声音？** 默认未启动输出；模拟、未审核和 1–2 级均不响。看审核回执，不直接连接原始风险与蜂鸣器。

**为什么没附原 `.toe`？** 原二进制可能缓存现场图像及本机路径。包中提供 TD 构建源码，在新工程生成 `SafetyHorizon.toe`，不分发现场缓存、不覆盖原工程。

**验收测试和生产部署是什么关系？** 作者已确认其现场及真实硬件验收测试完成。本公开源码候选版的可迁移性仍需按目标设备、平台和机位核验，不能将该现场结论直接推广到所有厂家或设备。具体部署条件见 `docs/07_FACTORY_ACCEPTANCE.md`；本项目未据此声称取得工业安全认证或可无条件直接投产。

**Windows 可以下载使用吗？** 可以获取源码，但当前包尚不能在 Windows 完整安装运行。窗口采集、RTSP 直连、本地视频三种 Windows 输入是已确认的适配需求，不是本版本已交付并实测的功能。不要把“可下载”理解为“已兼容”。

## 08 / Author & license · 作者及许可

**Safety Horizon--Lrj** · Copyright © 2026 · [MIT](LICENSE)。允许商业使用，但不构成认证、安全担保或第三方应用授权。项目仓库：[lrj2004424-star/safety-horizon](https://github.com/lrj2004424-star/safety-horizon)；问题反馈：[GitHub Issues](https://github.com/lrj2004424-star/safety-horizon/issues)。

Release 标签 **`v0.1.0-rc.1`**，标为 **Pre-release**，正文见 `RELEASE_NOTES.md`。下载与发布状态以 [GitHub Releases](https://github.com/lrj2004424-star/safety-horizon/releases) 为准；自动测试状态见 [Actions](https://github.com/lrj2004424-star/safety-horizon/actions)。
