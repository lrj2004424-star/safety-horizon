# 09 GitHub Release Checklist / 发布顺序

1. 检查 README、作者 Safety Horizon--Lrj、MIT、第三方说明和 08_VALIDATION 的限制是否真实。
2. 执行白名单打包脚本；审阅 FILE_INDEX、BUILD_MANIFEST 和 SHA256SUMS。
3. 在指定 GitHub 仓库初始化/提交，仅提交源码清单内文件。不要上传整个原运行工程。
4. 所有者已确认公开发布。仓库：[lrj2004424-star/safety-horizon](https://github.com/lrj2004424-star/safety-horizon)。仅上传发布清单中的内容，不上传现场数据。
5. GitHub Actions 的 macOS 与 Windows 测试均通过后创建标签 `v0.2.0-rc.1`，Release 选 **Pre-release**。
6. 正文用根目录 RELEASE_NOTES.md；仅附 `macOS-arm64.zip`、`Windows-x64.zip` 两个完整平台包及外部 `SHA256SUMS`。执行 `python scripts/package_release.py --platforms --output dist`；随后执行 `python scripts/verify_distribution.py --archives dist`，再核对下载回来的文件哈希。
7. 作者现场及硬件验收、91%–100% 准确率按 `docs/10_PROJECT_STATEMENT.md` 明确归属于作者报告；不要改写为第三方认证、全场景保证或无条件直接投产。不展示未实际运行的 CI / 性能徽章。

新 Release 发布并校验后，才移除旧 Release 的下载附件；保留旧标签、提交历史和本地备份。后续接口或依赖变更使用新版本。迁移只复制经授权的现场配置与数据，不复制旧 `.venv`，不要同时启动新旧实例。
