# 06 Troubleshooting / 故障排查

| 现象 | 检查顺序 |
|---|---|
| 找不到 Python | Route A 安装，或确认 python3.12 可用；不要改系统 Python 链接 |
| 下载失败 | 检查官方源网络、证书与代理；校验失败不可继续 |
| 模型缺失 | `scripts/setup_assets.py --download-models`，看哈希校验结果 |
| 采集助手不存在 | 安装 Apple Command Line Tools，再 `bash scripts/build_capture.sh` |
| 黑屏 / 旧画面 | 查看 `src/runtime/vision.log`，确认直播、授权和窗口；浏览器隐藏超过 2 秒的图像 |
| 没有骨架 | 机位、遮挡、目标尺寸和原始视频质量；不画假骨架 |
| 队列暂无片段 | 等待后录结束；刚启动缺前录要按真实状态处理 |
| 按钮无反应 | 当前页面是否带会话 token；审核服务是否 RUNNING；看明确回执 |
| 未确认成功 | 不要连续重复提交；查 review-supervisor 日志和命令回执 |
| 阈值无法应用 | 样本不足不是错误；要有 PROPOSED 和明确人工审批 |
| 没声音 | 默认 none，测试不响，1–2 级不响；开启提醒后仍需有效人工授权 |
| UNO 无法识别 | 正确固件、唯一受支持板卡、端口权限、拔插；不要盲目写任意串口 |
| 程序还在运行 | 关闭浏览器不会关闭后端，在启动终端 Ctrl+C |
| 地址已被占用 | 先关闭旧实例，或 `--port 8766`；不要随意杀陌生进程 |

排查先运行 `horizon.py doctor` 和 `horizon.py test`。报告问题时提供版本、系统、错误文本、步骤和脱敏日志，不上传员工视频或凭据。
