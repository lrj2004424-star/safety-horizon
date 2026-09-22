# 03 Human Review / 安全员审核程序

**输入**：风险状态 + 新鲜视频。**输出**：队列、目标前 3 秒/后 5 秒片段、人工判断与唯一的提醒授权。下一步：04 Storage / 06 Actuation。

服务源码：`src/safety_officer_review.py`、`src/safety_monitor/review_controller.py`、`review_contract.py`、`pre_event_buffer.py`。界面源码：`web_portal.py` + `ui/`，或 TD。

仅启动审核界面：`.venv/bin/python horizon.py run --no-vision`。仅启动服务供自行集成：`cd src` 后 `../.venv/bin/python safety_officer_review.py --project-root .`。没有上游状态时不生成假事件。

审核按钮通过唯一 command_id 与回执对接；不能把表面按钮/连线当作已落盘。真实记录需要持久化服务确认。
