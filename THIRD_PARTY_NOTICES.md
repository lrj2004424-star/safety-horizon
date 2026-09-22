# Third-party Software & Models / 第三方软件与模型

项目自身代码使用 MIT。依赖、模型、操作系统 SDK 和应用仍遵循各自许可证，MIT 不重新授权这些内容。源码包不捆绑原虚拟环境、商业软件或工厂数据。

| Component | 用途 / Use | 官方来源 |
|---|---|---|
| CPython 3.12 | 外部视觉 / 审核运行时 | https://www.python.org/ |
| uv | 托管 Python / 隔离依赖 | https://docs.astral.sh/uv/getting-started/installation/ |
| OpenCV | 图像和视频处理 | https://opencv.org/ |
| MediaPipe | 手部及身体关键点 | https://developers.google.com/edge/mediapipe/solutions/setup_python |
| MediaPipe models | 官方来源，安装时用固定 SHA-256 锁定内容 | https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker |
| NumPy | 数值计算 | https://numpy.org/ |
| pySerial | 可选串口 | https://pyserial.readthedocs.io/ |
| pywin32 | Windows 窗口信息及进程回收 | https://github.com/mhammond/pywin32 |
| MSS | Windows 指定视频区域采集 | https://github.com/BoboTiG/python-mss |
| tzdata | 两平台统一时区数据 | https://github.com/python/tzdata |
| TouchDesigner | 可选节点界面，单独账号/授权 | https://derivative.ca/download / https://derivative.ca/UserGuide/Licensing |
| 萤石 | 用户登录并授权的摄像头客户端 | https://www.ys7.com/ |
| Arduino | 可选固件工具及板卡 | https://www.arduino.cc/en/software |

模型不捆绑源码包。Lite/Hand 使用版本 1；Full 使用原工程对应的官方 latest 地址，但以固定 SHA-256 锁定内容，上游改变即停止，不能静默升级。uv 托管 Python 来自 python-build-standalone，并非本项目编译。第三方商标不表示其背书。
