"""MediaPipe Tasks hand-landmark adapter for OpenCV BGR frames."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from .geometry import Point


@dataclass(frozen=True)
class HandObservation:
    hands: list[list[Point]]
    confidence: float


class HandTracker:
    def __init__(
        self,
        model_path: str | Path,
        *,
        num_hands: int = 2,
        min_detection_confidence: float = 0.55,
        min_presence_confidence: float = 0.55,
        min_tracking_confidence: float = 0.55,
    ) -> None:
        model = Path(model_path)
        if not model.is_file():
            raise FileNotFoundError(
                f"Hand landmark model not found: {model}. See models/README.md."
            )
        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(
                model_asset_path=str(model),
                delegate=python.BaseOptions.Delegate.CPU,
            ),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._detector = vision.HandLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1

    def detect_observation(self, frame_bgr: np.ndarray, timestamp_ms: int) -> HandObservation:
        timestamp_ms = max(int(timestamp_ms), self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(rgb),
        )
        result = self._detector.detect_for_video(image, timestamp_ms)
        hands = [
            [(float(landmark.x), float(landmark.y)) for landmark in hand]
            for hand in result.hand_landmarks
        ]
        scores = [
            float(category.score)
            for handedness in result.handedness
            for category in handedness[:1]
        ]
        return HandObservation(hands=hands, confidence=min(scores) if scores else 0.0)

    def detect(self, frame_bgr: np.ndarray, timestamp_ms: int) -> list[list[Point]]:
        """Backward-compatible list-only API used by the first MVP."""
        return self.detect_observation(frame_bgr, timestamp_ms).hands

    def close(self) -> None:
        self._detector.close()

    def __enter__(self) -> "HandTracker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
