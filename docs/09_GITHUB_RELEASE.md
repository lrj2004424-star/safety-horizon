# 09 GitHub Release Checklist / 发布顺序

1. 检查 README、作者 Safety Horizon--Lrj、MIT、第三方说明和 08_VALIDATION 的限制是否真实。
2. 执行白名单打包脚本；审阅 FILE_INDEX、BUILD_MANIFEST 和 SHA256SUMS。
3. 在指定 GitHub 仓库初始化/提交，仅提交源码清单内文件。不要上传整个原运行工程。
4. 所有者已确认公开发布。仓库：[lrj2004424-star/safety-horizon](https://github.com/lrj2004424-star/safety-horizon)。仅上传发布清单中的内容，不上传现场数据。
5. GitHub Actions 测试通过后再创建标签 `v0.1.0-rc.1`，Release 选 **Pre-release**。
6. 标题和正文用根目录 RELEASE_NOTES.md；附 source.zip 和外部 SHA256SUMS。
7. 作者现场及硬件验收、91%–100% 准确率按 `docs/10_PROJECT_STATEMENT.md` 明确归属于作者报告；不要改写为第三方认证、全场景保证或无条件直接投产。不展示未实际运行的 CI / 性能徽章。

后续破坏接口或依赖变更需要新版本及迁移说明。完成全部现场验收并明确支持平台后再考虑稳定版，不为了展示把候选版改成 v1.0.0。
