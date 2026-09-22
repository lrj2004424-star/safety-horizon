#!/usr/bin/env python3
"""Minimal keyboard video annotation tool for six research labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

LABELS = {
    ord("1"): "正常操作",
    ord("2"): "接近风险",
    ord("3"): "手部危险停留",
    ord("4"): "身体姿态异常",
    ord("5"): "手部与冲头状态冲突",
    ord("6"): "遮挡或无法判断",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open {args.source}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    segments: list[dict[str, object]] = []
    start_s: float | None = None
    print("Space: mark start/end | 1..6: assign label | arrows/j-l: seek | q: save")
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frame_index = int(capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        time_s = frame_index / fps
        display = frame.copy()
        cv2.rectangle(display, (10, 10), (850, 84), (20, 22, 28), -1)
        cv2.putText(display, f"{time_s:.2f}s frame {frame_index} | start {start_s if start_s is not None else '-'}", (24, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 240, 240), 2)
        cv2.putText(display, "1 normal  2 approach  3 dwell  4 posture  5 conflict  6 uncertain", (24, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (220, 220, 220), 1)
        cv2.imshow("Video labeler", display)
        key = cv2.waitKey(0) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord(" "):
            start_s = time_s if start_s is None else start_s
        elif key in LABELS and start_s is not None:
            segments.append({"start_s": round(start_s, 3), "end_s": round(time_s, 3), "label": LABELS[key]})
            start_s = None
        elif key in (ord("j"), 81):
            capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index - int(fps)))
        elif key in (ord("l"), 83):
            capture.set(cv2.CAP_PROP_POS_FRAMES, min(frame_count - 1, frame_index + int(fps)))
    capture.release()
    cv2.destroyAllWindows()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "source_filename": Path(args.source).name, "labels": list(LABELS.values()), "segments": segments}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
