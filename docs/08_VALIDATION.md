# 08 Validation / 双平台验证记录

2026-09-22 · v0.2.0-rc.1 · Python 3.12 · Pre-release

## Automated verification / 已通过的软件检查

[可核对的双平台运行记录](https://github.com/lrj2004424-star/safety-horizon/actions/runs/35723516394)。最终发布提交也由同一 Actions 工作流检查，见仓库 Actions。

| 检查 | macOS | Windows |
|---|---|---|
| 环境 | macOS 14 ARM64 云端执行器 | Windows Server 2025 x64 云端执行器 |
| 锁定依赖安装及 pip check | 通过 | 通过 |
| 132 项核心测试 | 132 通过 | 131 通过；跳过 1 项 macOS 原生助手测试 |
| 16 项发布接口/平台测试 | 15 通过；跳过 1 项 Windows Job 测试 | 16 通过 |
| 三个真实模型下载、哈希及空白图推理 | 通过 | 通过 |
| 合成视频 → 实际姿态模型 → TEST 仪表盘 | 通过 | 通过 |
| 两个 ZIP 的逐文件校验 | 通过 | 通过 |
| 本平台 ZIP 解压后全部回归 | 通过，数量及跳过项同上 | 通过，数量及跳过项同上 |
| Windows 安装脚本及新 .venv 回归 | 不适用 | 通过（执行器已提供 uv） |

跨平台测试包括文件锁互斥、来源校验、裁切边界、RTSP 超时参数、审核服务启停、Windows 子进程强制回收；接口测试包含身份校验、落盘回执、重复提交保护、测试静音和视频解码。未通过降低业务断言来消除平台错误。

本机另使用独立 CPython 3.12.13 环境验证 macOS 解压副本；不复用原 TD 虚拟环境。受限沙盒会禁止本地 HTTP 端口，接口测试须在允许 localhost 的环境执行。

## Scope / 不混淆验证范围

- Windows 10/11 x64 是目标使用平台；云端 Windows Server 测试不等于每种 Windows 客户端和摄像头都已现场验收。
- 实际模型推理已运行，但合成视频不是员工或事故样本，测试通过率不是识别准确率。
- 未独立执行：Windows 萤石 PC 客户端实机采集、实际 RTSP 摄像头、双屏/DPI、真实 UNO 热插拔与声音、厂家整班运行，以及完全没有工具的新机全流程。
- macOS 原生采集助手在上一版本机已编译通过；本次不改原正式 TD 工程。Windows TD 节点工程不在本版支持路径。
- 包含白名单源码、文档、生成诊断音效；不包含现场录像、审核记录、账户、模型或旧虚拟环境。

## Author field report / 作者现场报告

Safety Horizon--Lrj 确认已完成其原现场及真实硬件验收，报告准确率 **91%–100%**。该结论归属于作者，不与上述自动化测试混算，也不自动延伸为新 Windows 适配或所有部署场景的性能保证。目标现场核验见 [07_FACTORY_ACCEPTANCE](07_FACTORY_ACCEPTANCE.md)。
