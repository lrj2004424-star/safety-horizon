# 05 Feedback / 样本分析与阈值建议程序

**输入**：04 的真实人工记录及抽检/漏报补录。**输出**：分类统计、保守评分阈值建议；人工审批才生效。

源码：`src/safety_monitor/threshold_optimizer.py`，服务动作见 `src/safety_officer_review.py`。

独立读记录生成建议（只打印，不回写）：在 `src` 下运行：

```bash
../.venv/bin/python -c 'from safety_monitor.threshold_optimizer import ThresholdOptimizer; import json; print(json.dumps(ThresholdOptimizer().propose("annotations"), ensure_ascii=False, indent=2))'
```

样本不足必须明确返回不足。阈值建议不是自动模型训练，不会自动优化所有距离/速度参数。仅有报警样本无法估计完整漏报率。
