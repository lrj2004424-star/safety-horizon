# 01 Capture / 视觉采集程序

**输入**：已授权的萤石实时窗口，或测试视频。**输出**：完整帧、左侧工位裁切、预览与图像新鲜度。下一步：02 Pose Risk。

源码：`src/ezviz_window_monitor.py`、`src/safety_monitor/window_capture_device.py`、`src/safety_monitor/input_router.py`、`src/macos_window_capture/`。

完整启动：在发布根目录运行 `.venv/bin/python horizon.py run`。单模块开发时在 `src` 下运行 `../.venv/bin/python ezviz_window_monitor.py --help`，显式使用 `--manifest config/ezviz-fatigue-single-upper.json`。缺少后续审核服务时，单独采集不会形成审核闭环。

需要 OpenCV/MediaPipe 依赖、官方模型；窗口输入额外需要 macOS 采集器、萤石登录和录屏权限。不能把摄像头硬件/客户端捆绑成 Python 包。
