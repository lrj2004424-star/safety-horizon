# 10 Project & Test Statement / 项目介绍与测试说明

Documentation revision / 文档修订：2026-09-22 · v0.1.0-rc.1 · Safety Horizon--Lrj

## Public introduction / 对外项目介绍

**Safety Horizon 安全视界**是由 **Safety Horizon--Lrj** 设计的裁床作业视觉风险辅助监测与人工审核系统。项目以“可见的证据、可解释的判断、可追溯的处置”为设计主线，将视频采集、姿态分析、风险评分、事件录像、安全员标注和审核后提醒串联成完整工作流。

项目作者确认已完成现场及真实硬件验收测试，并报告其测试中的准确率为 **91%–100%**。系统结合视觉分析与安全员人工复核，支持风险预警、事件回看与审核记录追溯。该区间属于作者报告的测试结果，不代表所有机位、工况或操作系统下的统一性能保证。

Safety Horizon is a visual risk-assistance and human-review system designed by **Safety Horizon--Lrj** for cutting-table operations. It connects video acquisition, pose analysis, risk scoring, event evidence, annotations and review-authorized alerts. The author reports completing field and physical-hardware acceptance tests, with reported test accuracy ranging from **91% to 100%**. These are author-reported results, not a guarantee across deployments or operating systems.

## Read the results correctly / 正确理解验证结果

| 表述 | 实际含义 | 不代表什么 |
|---|---|---|
| 作者完成现场与真实硬件验收测试 | 作者对其测试现场和硬件提供的结论 | 不等于本公开发布副本已在每家厂家重新验收 |
| 作者报告准确率 91%–100% | 作者提供的测试区间 | 不等于 100% 检出所有危险、零漏报或各子功能均达到该范围 |
| 141 / 141 软件测试通过 | 解压副本在本机独立 Python 环境的自动化检查结果 | 不是 141 个真实事故样本，也不是现场识别准确率 |
| 人工审核 | 安全员结合事件证据作出判断和处置 | 不能保证发现未进入队列的所有漏报，也不能替代机器防护 |
| MIT 许可 | 项目代码的使用和分发许可 | 不等于工业安全认证、第三方软件授权或生产准入 |

## Evidence scope / 技术记录范围

作者声明于 2026-09-22 提供。当前交付材料没有包含准确率的指标定义、样本量、统计分母、混淆矩阵、测试版本和硬件型号清单；发布整理过程未独立复算该区间。不得将未收到的记录填写成已核验，或捏造第三方认证。详细软件实测结果见 [08_VALIDATION](08_VALIDATION.md)，目标现场核验见 [07_FACTORY_ACCEPTANCE](07_FACTORY_ACCEPTANCE.md)。

## Platform & deployment / 平台与部署

本候选包提供 **Apple Silicon macOS** 安装运行路径和可选 TD 构建源码。Windows 三种输入方式（萤石 PC 窗口 / RTSP / 本地视频）属于已确认的适配需求，**不是当前版本已完成的 Windows 支持**。平台选择见 [01_INSTALL](01_INSTALL.md)。

系统用于风险辅助观察，不替代机器联锁、现场安全制度、专业安全评估或员工必要防护。不将作者自测结论表述成工业安全认证，也不据此承诺可在任意现场无条件直接投产。
