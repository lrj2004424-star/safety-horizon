#!/usr/bin/env python3
"""Summarize one labeled offline run without claiming industrial accuracy."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--demo-video", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = list(csv.DictReader(Path(args.features).open(encoding="utf-8")))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_count = len(rows)
    state_counts = Counter(row["state"] for row in rows)
    raw_state_counts = Counter(row["raw_state"] for row in rows)
    reason_counts = Counter(row["reason"] for row in rows)
    machine_cues = Counter(row["machine_raw_state"] for row in rows)
    hand_any = sum(int(row["hand_count"]) >= 1 for row in rows)
    hand_two = sum(int(row["hand_count"]) >= 2 for row in rows)
    pose_any = sum(float(row["pose_visibility"]) > 0 for row in rows)
    pose_full = sum(row.get("pose_upper_body_visible") == "True" for row in rows)
    summary = {
        "scope": "one 20.783-second course-research video; not an industrial accuracy evaluation",
        "frames": frame_count,
        "state_counts": dict(state_counts),
        "raw_state_counts": dict(raw_state_counts),
        "reason_counts": dict(reason_counts),
        "machine_visual_cues_unverified": dict(machine_cues),
        "availability": {
            "at_least_one_hand_frames": hand_any,
            "at_least_one_hand_rate": _rate(hand_any, frame_count),
            "two_hand_frames": hand_two,
            "two_hand_rate": _rate(hand_two, frame_count),
            "pose_detected_frames": pose_any,
            "pose_detected_rate": _rate(pose_any, frame_count),
            "full_required_upper_body_frames": pose_full,
            "full_required_upper_body_rate": _rate(pose_full, frame_count),
        },
        "manual_segments": labels["segments"],
        "confirmed_risk_segments": 0,
        "accuracy_claim": None,
        "machine_state_claim": "UNCERTAIN until empty-cycle calibration and a machine-state ground truth are provided",
    }
    (output_dir / "test-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = _markdown(summary)
    (output_dir / "test-summary.md").write_text(report, encoding="utf-8")
    normal_index = _select_frame(rows, 0.0, 6.6, normal=True)
    uncertain_index = _select_frame(rows, 6.6, 8.5, normal=False)
    _comparison(
        Path(args.demo_video),
        normal_index,
        uncertain_index,
        output_dir / "normal-vs-uncertain-comparison.png",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _rate(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0.0


def _select_frame(rows: list[dict[str, str]], start: float, end: float, *, normal: bool) -> int:
    candidates = [row for row in rows if start <= float(row["timestamp_s"]) <= end]
    if normal:
        key = lambda row: (
            int(row["hand_count"]),
            row.get("pose_upper_body_visible") == "True",
            float(row["pose_visibility"]),
        )
    else:
        key = lambda row: (
            float(row.get("machine_reference_motion") or 0.0),
            -int(row["hand_count"]),
        )
    return int(max(candidates, key=key)["frame_index"])


def _read_frame(capture: cv2.VideoCapture, index: int) -> np.ndarray:
    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = capture.read()
    if not ok or frame is None:
        raise RuntimeError(f"Could not read frame {index}")
    return frame


def _comparison(video: Path, normal_index: int, uncertain_index: int, output: Path) -> None:
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open {video}")
    left = _read_frame(capture, normal_index)
    right = _read_frame(capture, uncertain_index)
    fps = capture.get(cv2.CAP_PROP_FPS) or 23.0
    capture.release()
    left = cv2.resize(left, (720, 405), interpolation=cv2.INTER_AREA)
    right = cv2.resize(right, (720, 405), interpolation=cv2.INTER_AREA)
    canvas = np.full((520, 1460, 3), (25, 28, 34), dtype=np.uint8)
    canvas[85:490, 10:730] = left
    canvas[85:490, 730:1450] = right
    cv2.rectangle(canvas, (10, 85), (729, 489), (80, 190, 110), 4)
    cv2.rectangle(canvas, (730, 85), (1449, 489), (185, 90, 180), 4)
    cv2.putText(canvas, "A  MANUAL: NORMAL OPERATION", (25, 42), cv2.FONT_HERSHEY_DUPLEX, 0.8, (95, 225, 130), 2, cv2.LINE_AA)
    cv2.putText(canvas, "System still UNCERTAIN: machine cue unverified", (25, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (220, 220, 220), 1, cv2.LINE_AA)
    cv2.putText(canvas, "B  MANUAL: CANNOT JUDGE", (750, 42), cv2.FONT_HERSHEY_DUPLEX, 0.8, (210, 115, 205), 2, cv2.LINE_AA)
    cv2.putText(canvas, "Camera reframing / ROI drift", (750, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (220, 220, 220), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"t={normal_index / fps:.2f}s", (25, 515), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"t={uncertain_index / fps:.2f}s", (750, 515), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (235, 235, 235), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"Could not write {output}")


def _markdown(summary: dict[str, object]) -> str:
    availability = summary["availability"]
    return f"""# 视频·21 离线检测结果

- 处理：{summary['frames']} 帧 / 20.783 秒，1280×720，约 23 fps。
- 联合状态：{summary['state_counts']}。由于机器区未有空机循环与真值标定，系统全程保守返回 `UNCERTAIN`，未输出“安全”或真实 `DANGER` 结论。
- 手部：至少一只手 {availability['at_least_one_hand_frames']} 帧（{availability['at_least_one_hand_rate']:.1%}）；同时两只手 {availability['two_hand_frames']} 帧（{availability['two_hand_rate']:.1%}）。
- Pose：检出人体 {availability['pose_detected_frames']} 帧（{availability['pose_detected_rate']:.1%}）；所有要求的肩/肘/腕/髋同时达标 {availability['full_required_upper_body_frames']} 帧（{availability['full_required_upper_body_rate']:.1%}）。
- 机器视觉方向线索（未验证）：{summary['machine_visual_cues_unverified']}。该数据只用于下轮标定，不当作冲头状态。
- 人工复核未确认“接近风险/危险停留/姿态异常/手部—冲头冲突”片段，因此不计算风险召回率、提前量或工业准确率。
"""


if __name__ == "__main__":
    raise SystemExit(main())
