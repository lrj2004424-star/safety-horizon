# 04 Annotation Storage / 标注与存储程序

**输入**：人工审核记录。**输出**：原始 JSON、日期/工位导出 CSV/JSONL、队列和审计。下一步：05 Feedback。

源码：`src/safety_monitor/review_store.py`、`review_contract.py`。这是共享库，不是必须独立常驻的应用；审核服务调用它可避免两份进程重复写入。

测试：`cd src` 后 `../.venv/bin/python -m unittest discover -s tests -p 'test_review_workflow.py' -v`。

发布源码不附现场 annotations。不能将审核记录提交 GitHub。文件结构/字段详见 `docs/03_WORKFLOW_API.md`。
# Platform commands / 平台命令

以下 `.venv/bin/python` 是 macOS 写法。Windows 在项目根目录用 `.venv\Scripts\python.exe`，进入 `src` 后用 `..\.venv\Scripts\python.exe`；命令其余参数相同（使用 PowerShell）。完整工作流优先使用对应平台的 `02_run` 菜单，不单独运行模块。
