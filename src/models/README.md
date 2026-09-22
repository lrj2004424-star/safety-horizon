# Official Models / 官方模型

从发布根目录运行 `.venv/bin/python scripts/setup_assets.py --download-models`。
脚本从 Google 官方下载并以 SHA-256 固定文件内容：Lite/Hand 使用版本 1，Full 保留原工程验证的 latest 文件内容；上游内容改变即拒绝安装，不自动替换模型。
二进制模型不进入源码包；不复用原工程中来源不明确的模型副本。
