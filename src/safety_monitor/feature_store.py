"""Frame-level anonymous feature export for labeling and lightweight baselines."""

from __future__ import annotations

import csv
from pathlib import Path

from .joint_engine import JointAssessment


class FeatureStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", newline="", encoding="utf-8")
        self._writer: csv.DictWriter[str] | None = None

    def append(self, frame_index: int, timestamp_s: float, assessment: JointAssessment) -> None:
        row = {
            "frame_index": frame_index,
            "timestamp_s": round(timestamp_s, 4),
            **assessment.feature_dict(),
            "contains_face_pixels": False,
            "contains_identity": False,
        }
        if self._writer is None:
            self._writer = csv.DictWriter(self._stream, fieldnames=list(row))
            self._writer.writeheader()
        self._writer.writerow(row)

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "FeatureStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
