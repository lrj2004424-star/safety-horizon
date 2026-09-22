#!/usr/bin/env python3
"""Read-only deployment preflight. Never captures video or activates a buzzer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time


def inspect_project(root: Path) -> dict:
    root = root.resolve()
    checks = []
    def add(name, ok, detail, *, warning=False):
        checks.append({"check": name, "status": "PASS" if ok else "WARN" if warning else "BLOCKED", "detail": detail})

    toe = root / "touchdesigner/SafetyHorizon.toe"
    add("正式工程", toe.is_file() and toe.stat().st_size > 1000, str(toe))
    manifest = root / "config/ezviz-fatigue-single-upper.json"
    try:
        config = json.loads(manifest.read_text(encoding="utf-8"))
        stations = [s for s in config["stations"] if s.get("enabled") is True]
        add("左侧单工位", len(stations) == 1 and stations[0].get("station_id") == "upper_station", f"启用工位数 {len(stations)}")
        add("现场区域标定", bool(stations) and all(s.get("zones_calibrated") is True for s in stations),
            "必须经过现场标定与验证；不得仅改配置标志冒充完成")
    except (OSError, ValueError, KeyError, TypeError) as error:
        add("工位配置", False, type(error).__name__)
    for name in ("cv2", "mediapipe", "numpy", "serial"):
        add(f"依赖 {name}", importlib.util.find_spec(name) is not None, "仅检查当前解释器可定位模块；不等同运行验证")
    for name in ("pose_landmarker_full.task", "pose_landmarker_lite.task", "hand_landmarker.task"):
        path = root / "models" / name
        add(f"模型 {name}", path.is_file() and path.stat().st_size > 1000, "检查模型文件存在且非空")
    free_gb = shutil.disk_usage(root).free / 1024 ** 3
    add("录像磁盘余量", free_gb >= 10, f"可用 {free_gb:.1f} GiB；建议至少保留 10 GiB 并制定保留周期")
    for name, relative, max_age in (
        ("视觉状态新鲜度", "artifacts/ezviz-window-captures/live-status.json", 1.5),
        ("审核服务新鲜度", "runtime/safety-officer-review-state.json", 3.0),
        ("审核守护进程新鲜度", "runtime/review-supervisor.json", 3.0),
        ("视频输出新鲜度", "runtime/touchdesigner-opencv-result.jpg", 1.5),
    ):
        path = root / relative
        age = time.time() - path.stat().st_mtime if path.is_file() else None
        add(name, age is not None and 0 <= age <= max_age,
            "缺少运行文件" if age is None else f"文件年龄 {age:.2f} 秒；仅文件新鲜度，仍需目视验证真视频")
    for name, detail in (
        ("现场综合验收", "尚需实测：重启自动采集、真实骨架、审核按钮、逐帧回放、标注落盘和声音一次性触发"),
        ("Arduino 连接恢复", "唯一受支持 UNO 的自动发现/重连及旧报警抑制已有模拟测试；真实板卡热插拔仍待验收"),
        ("长时间与故障验收", "尚需完整班次试运行，断网、锁屏、磁盘不足、串口拔出和进程异常退出试验"),
        ("安装可迁移性", "现有启动器及虚拟环境含本机绝对路径；厂家新机器必须重新安装和验证"),
    ):
        add(name, False, detail)
    hashes = {}
    for relative in (
        "safety_officer_review.py", "safety_monitor/review_contract.py", "safety_monitor/review_store.py",
        "safety_monitor/review_controller.py", "indicator_bridge.py", "computer_buzzer_simulator.py",
        "touchdesigner/td_runtime.py", "touchdesigner/SafetyHorizon.toe",
        "supervise_service.py", "start_touchdesigner_engine.command", "safety_monitor/frame_activity.py",
        "ezviz_multistation_fatigue_monitor.py", "safety_monitor/serial_connection.py", "safety_monitor/__init__.py",
    ):
        path = root / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "project_root": str(root),
        "release_status": "NOT_APPROVED_FOR_PRODUCTION",
        "scope": "辅助预警原型；不是机器联锁或已验证的员工受伤识别系统",
        "python": sys.version.split()[0], "checks": checks, "sha256": hashes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, help="Optional explicitly requested JSON report path")
    args = parser.parse_args()
    report = inspect_project(args.project_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        from safety_monitor.review_store import ReviewStore
        ReviewStore._write_json_atomic(args.output.resolve(), report)
    return 2 if any(c["status"] == "BLOCKED" for c in report["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
