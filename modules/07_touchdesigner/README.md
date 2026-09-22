# 07 TouchDesigner / 可视化工作流适配器

**输入**：同一套核心状态、视频、队列与审核契约。**输出**：节点界面、流程连接和审核操作。不是独立视觉算法。

源码：`src/touchdesigner/build_touchdesigner_project.py`、`src/touchdesigner/td_runtime.py`。安装和运行严格按 `docs/04_TOUCHDESIGNER.md`。

原正式 `.toe`、原节点和原工作流没有被这次发布整理删除或重建。发布包不带含现场缓存的旧二进制，由用户在空白工程生成。新工程的位置、按钮可用性和重启行为需 TD 实机验收，不能由 Python 单元测试代替。
