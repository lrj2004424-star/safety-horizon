# Safety Horizon v0.2.0-rc.1 — macOS / Windows

**两个电脑平台，同一套视觉风险与人工审核工作流。** Safety Horizon--Lrj · MIT · Pre-release。

## 下载

- 苹果 M 系列电脑：`SafetyHorizon-v0.2.0-rc.1-macOS-arm64.zip`
- Windows 10/11 x64：`SafetyHorizon-v0.2.0-rc.1-Windows-x64.zip`
- `SHA256SUMS`：附件校验清单。

每包先读 `00_START_HERE.md`，均含完整共享源码，不要求安装 TouchDesigner。

## 更新

- 两平台独立安装、启动、测试入口及依赖锁。
- 输入可选萤石窗口、RTSP、本地视频；测试不驱动真实输出。
- Windows 增加区域采集、文件互斥、退出回收和系统声音支持。
- 保留视频证据、逐帧标注、人工审核、存储及审核后提醒规则。

## 验证与限制

macOS ARM64 与 Windows x64 云端回归均已通过：每平台 147 项通过、1 项非本平台测试跳过；含真实模型推理、合成视频处理和解压副本回归。Windows 安装脚本创建的新环境亦通过检查。记录见 [Actions](https://github.com/lrj2004424-star/safety-horizon/actions/runs/35723516394)，范围见仓库 `docs/08_VALIDATION.md`。

这些是软件验证，不代替实际萤石客户端、摄像头、板卡和目标现场验收。

Windows 窗口视频区域需可见、无遮挡；RTSP 需要设备支持和授权。新机位须重新标定。Windows 使用浏览器界面，不提供已验收的 Windows TD 节点工程。

作者报告的原现场准确率 91%–100% 不自动代表新增 Windows 适配结果。系统不能替代机器联锁和安全防护。

## 更新方式

停止旧实例，保留旧数据，解压新包到新目录并重新安装；不复用旧 .venv，不自动覆盖审核记录。先用测试视频核验，再接实时来源。默认静音。
