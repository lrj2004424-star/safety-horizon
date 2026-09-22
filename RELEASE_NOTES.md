# Safety Horizon v0.1.0-rc.1 — Portable Review Workflow

发布标签：`v0.1.0-rc.1`；标题：`Safety Horizon v0.1.0-rc.1 — 本地视觉风险与人工审核工作流`；**勾选 Pre-release**。

## Highlights / 本次功能

- 既有单工位分析、证据缓存、人工审核、标注存储、阈值建议和审核后提醒整理为可移植源码。
- 新增不依赖 TD 的本地浏览器审核：事件选择、逐帧回放、帧标注、评定、人工补录及真实回执。
- 一套审核后授权逻辑，默认静音；模拟样本与真实现场统计隔离。
- Apple Silicon Mac 引导安装和已有 Python 3.12 两条路线；依赖版本及哈希锁定，模型固定版本校验。
- 中英文编号说明、逐文件索引、接口文档、GitHub 自动回归配置及厂家验收清单。
- 作者 **Safety Horizon--Lrj**，项目代码采用 MIT。

## Download / 下载

`SafetyHorizon-v0.1.0-rc.1-source.zip`：源码、自动安装器、浏览器/TD 入口和文档。已有环境用 README Route B，无环境用 Route A，不重复维护两个核心代码版本。

各模块按 `modules/01_capture` 至 `07_touchdesigner` 分区；它们依赖共享 `src/`，不声称一个孤立脚本可以代替所有依赖。`SHA256SUMS` 用于完整性校验。

## Validation / 测试

项目作者 Safety Horizon--Lrj 确认已完成现场及真实硬件验收测试，报告测试准确率为 91%–100%。这是作者报告的现场结果，不是本次 141 项自动化测试得出的准确率，不代表跨场景、跨平台保证或工业安全认证。介绍全文见 `docs/10_PROJECT_STATEMENT.md`。

以 `docs/08_VALIDATION.md` 为准，区分新环境安装、代码回归、模型、浏览器接口、原生编译及真实现场验收。GitHub 自动回归的实际结果以 [Actions 运行记录](https://github.com/lrj2004424-star/safety-horizon/actions) 为准，不把 CI 通过等同于现场准确率验证。

## Known limitations / 限制

- 本包的安装运行路径为 Apple Silicon macOS；Windows 适配未完成，Linux/Intel Mac 不在本次验证范围。
- 窗口输入依赖权限、播放器、网络和机位；文件更新时间不能证明不是回放。
- TD 源码生成的新工程仍需实机交互验收；不附现场 `.toe` 缓存。
- 作者已报告现场与真实硬件验收完成；本次发布回归没有独立复核该现场测试，也没有据此验收厂家新机、整班性能或不同部署机位。不能替代机器联锁或确认员工受伤。

## Upgrade / 更新

解压到新目录，保留原工程与数据。停止旧实例，再用测试视频回归。审核记录迁移需单独备份、核验，不自动合并、删除或公开。
