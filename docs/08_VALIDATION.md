# 08 Validation Record / 干净环境验证记录

Date / 日期：2026-09-21。Release / 版本：v0.1.0-rc.1。

Documentation update / 文档更新：2026-09-22，仅更新验收说明，未修改程序代码。

## Author field report / 作者现场测试报告

Safety Horizon--Lrj 确认已完成现场及真实硬件验收测试，报告测试准确率为 **91%–100%**。此为作者提供的测试结论，与下表由本次发布过程执行的软件检查分开记录。

该报告没有在本包中附带统计指标定义、样本量、分母、混淆矩阵、具体测试版本与硬件清单；本次文档修订未独立复算该准确率。作者计划后续拍摄现场证据。不能据此将 141 项测试通过率当作识别准确率，或把作者结论扩展到 Windows、新工厂和任意工况。

## Environment / 环境

- Apple Silicon M4 Pro，macOS 26.6.2；同一台 Mac 的新环境，不是厂家另一台机器。
- 独立下载 CPython 3.12.13，建立全新虚拟环境；没有复制原环境、没有开启 system-site-packages。
- 从 PyPI 按 requirements.lock 的版本和哈希安装 20 个依赖；`pip check` 无冲突。
- 原正式 TD 工程未修改。模型从 Google 官方源重新下载并按原模型哈希核验，不把旧虚拟环境塞进包中。

## Results / 已执行检查

| Test | Result | Scope / 边界 |
|---|---|---|
| Existing regression | 132 项通过 | 原视觉、审核、存储和模拟硬件逻辑 |
| Release integration | 9 项通过 | HTTP 身份/Origin/Host校验、过期状态、回执、落盘、重复提交、模拟静音、逐帧解码、保护原图 |
| Dependency installation | PASS | 新 Python + 锁定安装 + pip check |
| Model checksums | 3 / 3 PASS | Full Pose、Lite Pose、Hand 与原工程模型哈希相同 |
| Actual model load/inference | 3 / 3 PASS | 真实模型在合成空白图推理，无伪造人体/手部；不是实际工人检测准确率测试 |
| Native build | PASS | CoreGraphics C、窗口枚举 C、ScreenCaptureKit Swift 本机源码编译 |
| Browser UI | PASS for inspected state | RUNNING/TEST/无视频等待状态、按钮回执、禁止不足样本审批；1280px 宽无面板横向溢出 |
| Public file allowlist | PASS at packaging | 仅源码、生成诊断音效、文档与元数据；不带现场录像/账户/annotations/缓存/模型/venv |
| Extracted archive regression | 141 / 141 PASS | 发布 ZIP 解压到独立临时目录，使用上述新 Python 环境；132 项核心 + 9 项发布接口测试，进程退出码 0，文件 SHA-256 全部一致 |

## Issues found and corrected / 本次确实发现并修复

1. 原部署依赖本机已存在的诊断音效；发布副本现由项目生成脚本生成，再包含到包内。
2. Swift 单独编译需要 `-parse-as-library`；构建脚本补齐，编译通过。
3. Full Pose 的官方 `/1` 文件与原工程 `/latest` 内容不同。保留原模型哈希；Full 从官方 latest 下载但严格核验固定哈希，上游变化即拒绝，不悄悄更换模型。
4. 新增独立界面必须有真实命令回执、旧命令互斥及本机接口权限，不能仅画按钮。
5. 样本不足时文案明确说明不能应用建议，不误写为已产生 PROPOSED。

模型首次在受限沙盒中因系统图形服务不可用终止；在获得正常图形访问权限后复测通过。不能将沙盒内失败隐藏为成功，也不据此更换模型。

## Outside this release verification / 本次发布验证未覆盖

作者已报告现场和硬件验收，本次发布过程并未独立执行或复核这些现场步骤。完整引导脚本在“全无工具的新 Mac”从零双击、另一台厂家机器、Windows/Linux/Intel、真实 UNO 热插拔、摄像头直播真实性、现场机位/风险标定、整班性能，以及新生成 TD 工程的界面/按钮/退出行为，不能由本次代码回归代替逐项验收。详见 07_FACTORY_ACCEPTANCE。

本包继续标为 **Pre-release 软件候选版**。本文不是生产准入或工业安全认证文件；准确率区间仅按作者报告归属陈述，不作零漏报、全平台即用或任意现场可直接投产的承诺。
