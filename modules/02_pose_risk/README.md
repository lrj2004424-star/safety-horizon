# 02 Pose & Risk / 身体识别与风险程序

**输入**：01 的单工位视频。**输出**：骨架、姿态特征、风险建议和不确定状态。下一步：03 Review。

源码：`src/ezviz_multistation_fatigue_monitor.py`、`src/safety_monitor/fatigue_engine.py`、`src/safety_monitor/multistation_fatigue.py` 及同目录的姿态、几何模块。

开发参数：`cd src` 后 `../.venv/bin/python ezviz_multistation_fatigue_monitor.py --help`。完整启动使用根目录 `horizon.py run`，不要将默认多工位配置误当成当前单工位配置。

此模块产生的是辅助风险，不是疲劳医学诊断或已发生伤害。缺失骨架、不可靠机位、遮挡不能判定安全。
