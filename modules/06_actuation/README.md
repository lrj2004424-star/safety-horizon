# 06 Reviewed Alerts / 审核后蜂鸣器程序

**唯一输入**：`src/runtime/review-actuator.json`，由人工审核服务生成。**输出**：单次两短一长或静音；不连接机器控制。

电脑程序：`src/computer_buzzer_simulator.py`；串口程序：`src/indicator_bridge.py`、`src/safety_monitor/serial_connection.py`。
固件：`src/hardware/arduino_status_indicator/arduino_status_indicator.ino`；基础蜂鸣器诊断另见 `arduino_buzzer_basic_test`，它不是审核固件。

默认无声。现场检查后在根目录执行 `horizon.py run --alerts computer` 或 `--alerts uno`。只有人工有效授权才响，不允许直接接原始 risk_score。未连接板卡不等于模拟板卡测试已通过。

**历史程序注意**：`app.py`、`ezviz_monitor.py`、`fatigue_window_monitor.py` 等研究入口保留用于开发，可能有自己的声音策略；它们不是本次统一审核发布入口，不用于现场直接替代 `horizon.py run`。
