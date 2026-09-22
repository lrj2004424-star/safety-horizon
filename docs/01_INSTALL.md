# 01 Installation / 安装分流

按根目录 README 的 Route A（无环境）或 Route B（已有 Python）执行。两个入口共用同一个 `requirements.lock`，不会捆绑两个不同引擎。

## Platform selection / 先确认操作系统

| 平台 | 本包实际交付状态 | 应如何选择 |
|---|---|---|
| Apple Silicon macOS | 提供引导安装、独立浏览器入口和原生窗口采集源码；本机独立环境回归通过 | 按 README 安装，再核验自己的机位与设备 |
| Windows | 尚未完成适配；当前文件锁、窗口采集和启动脚本含 macOS/POSIX 依赖 | 不要运行 `.command` 或把源码可下载理解为 Windows 可运行 |
| Intel Mac / Linux | 不在本次全链路验证范围 | 不标记为即装即用 |

Windows 的确认需求是：用户可选萤石 PC 窗口、RTSP 或本地视频。它们是后续适配范围，当前包未提供已验收的 Windows 安装器与完整运行链路。Windows 与 macOS 共用项目名称不等于已实现相同平台支持。

## Software roles / 软件职责

| 软件 | 必需性 | 获取方式 |
|---|---|---|
| Python 3.12 | 核心运行时 | 引导脚本通过 uv 自动隔离安装，或自备 |
| OpenCV / MediaPipe / NumPy / pySerial | Python 依赖 | 自动按带哈希的锁文件安装 |
| 模型文件 | 身体与手部识别 | 官方固定版本下载与校验 |
| 浏览器 | 独立审核台 | 使用本机已有浏览器 |
| Apple Command Line Tools | 只在实时窗口采集时需要 | `xcode-select --install`，手动确认 |
| 萤石客户端 | 当前实时窗口方案需要；本地视频测试不需要 | 用户官方下载安装、登录并选择实时画面 |
| TouchDesigner | 可选图形化工作流 | 官方安装、账号及合适许可 |
| Arduino IDE / CLI | 只在烧录板卡时需要 | 官方安装，固件见 `modules/06_actuation` |
| PyCharm / cvzone | 非必需 | 本发布流程不安装 |

安装器不会替用户购买授权、登录账户、修改隐私权限、安装未知驱动或关闭系统保护。若网络下载失败，查看错误并重试，不使用来源不明的模型/二进制替代。

## Relocation / 移动文件夹

源代码使用相对位置定位。`.venv` 不能当作可随意搬运的安装包；把源码移到新位置后重新建环境。TD 工程应在最终位置重新生成。终端路径带空格时加双引号。

## Existing environments / 已有环境

先用 `python -m pip check` 检查冲突。不要在重要工作环境里直接运行 `uv pip sync`，它可能移除锁文件外的包；引导脚本只对项目 `.venv` 执行该操作。推荐总是使用独立 `.venv`。
