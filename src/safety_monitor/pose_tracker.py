"""MediaPipe Pose Landmarker adapter and upper-body feature extraction."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from .geometry import Point

LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_ELBOW, RIGHT_ELBOW = 13, 14
LEFT_WRIST, RIGHT_WRIST = 15, 16
LEFT_HIP, RIGHT_HIP = 23, 24

UPPER_BODY_CONNECTIONS = (
    (LEFT_SHOULDER, RIGHT_SHOULDER),
    (LEFT_SHOULDER, LEFT_ELBOW),
    (LEFT_ELBOW, LEFT_WRIST),
    (RIGHT_SHOULDER, RIGHT_ELBOW),
    (RIGHT_ELBOW, RIGHT_WRIST),
    (LEFT_SHOULDER, LEFT_HIP),
    (RIGHT_SHOULDER, RIGHT_HIP),
    (LEFT_HIP, RIGHT_HIP),
)


@dataclass(frozen=True)
class PosePoint:
    x: float
    y: float
    visibility: float
    presence: float


@dataclass(frozen=True)
class PoseObservation:
    landmarks: tuple[PosePoint, ...]
    shoulder_center: Point | None
    hip_center: Point | None
    torso_center: Point | None
    left_elbow_angle: float | None
    right_elbow_angle: float | None
    torso_lean_deg: float | None
    upper_body_visible: bool
    visibility_score: float


class PoseTracker:
    def __init__(
        self,
        model_path: str | Path,
        *,
        num_poses: int = 1,
        min_detection_confidence: float = 0.5,
        min_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        min_landmark_visibility: float = 0.45,
        image_mode: bool = False,
    ) -> None:
        model = Path(model_path)
        if not model.is_file():
            raise FileNotFoundError(f"Pose landmark model not found: {model}")
        self._image_mode = bool(image_mode)
        options = vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(
                model_asset_path=str(model),
                delegate=python.BaseOptions.Delegate.CPU,
            ),
            running_mode=(
                vision.RunningMode.IMAGE
                if self._image_mode
                else vision.RunningMode.VIDEO
            ),
            num_poses=max(1, int(num_poses)),
            min_pose_detection_confidence=min_detection_confidence,
            min_pose_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
            output_segmentation_masks=False,
        )
        self._detector = vision.PoseLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1
        self.min_landmark_visibility = min_landmark_visibility

    def detect(self, frame_bgr: np.ndarray, timestamp_ms: int) -> PoseObservation | None:
        observations = self.detect_observations(frame_bgr, timestamp_ms)
        return observations[0] if observations else None

    def detect_observations(
        self, frame_bgr: np.ndarray, timestamp_ms: int
    ) -> list[PoseObservation]:
        timestamp_ms = max(int(timestamp_ms), self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(rgb),
        )
        result = (
            self._detector.detect(image)
            if self._image_mode
            else self._detector.detect_for_video(image, timestamp_ms)
        )
        if not result.pose_landmarks:
            return []
        observations: list[PoseObservation] = []
        for detected_pose in result.pose_landmarks:
            landmarks = tuple(
                PosePoint(
                    x=float(item.x),
                    y=float(item.y),
                    visibility=float(item.visibility or 0.0),
                    presence=float(item.presence or 0.0),
                )
                for item in detected_pose
            )
            observations.append(
                build_pose_observation(landmarks, self.min_landmark_visibility)
            )
        return observations

    def close(self) -> None:
        self._detector.close()

    def __enter__(self) -> "PoseTracker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build_pose_observation(
    landmarks: tuple[PosePoint, ...], min_visibility: float = 0.45
) -> PoseObservation:
    required = (
        LEFT_SHOULDER,
        RIGHT_SHOULDER,
        LEFT_ELBOW,
        RIGHT_ELBOW,
        LEFT_WRIST,
        RIGHT_WRIST,
        LEFT_HIP,
        RIGHT_HIP,
    )
    visibility = [landmarks[index].visibility for index in required]
    upper_body_visible = all(value >= min_visibility for value in visibility)
    shoulder_center = _midpoint(landmarks, LEFT_SHOULDER, RIGHT_SHOULDER, min_visibility)
    hip_center = _midpoint(landmarks, LEFT_HIP, RIGHT_HIP, min_visibility)
    torso_center = (
        ((shoulder_center[0] + hip_center[0]) / 2, (shoulder_center[1] + hip_center[1]) / 2)
        if shoulder_center is not None and hip_center is not None
        else None
    )
    torso_lean = None
    if shoulder_center is not None and hip_center is not None:
        dx = shoulder_center[0] - hip_center[0]
        dy = hip_center[1] - shoulder_center[1]
        torso_lean = math.degrees(math.atan2(dx, max(1e-6, dy)))
    return PoseObservation(
        landmarks=landmarks,
        shoulder_center=shoulder_center,
        hip_center=hip_center,
        torso_center=torso_center,
        left_elbow_angle=_joint_angle(
            landmarks, LEFT_SHOULDER, LEFT_ELBOW, LEFT_WRIST, min_visibility
        ),
        right_elbow_angle=_joint_angle(
            landmarks, RIGHT_SHOULDER, RIGHT_ELBOW, RIGHT_WRIST, min_visibility
        ),
        torso_lean_deg=torso_lean,
        upper_body_visible=upper_body_visible,
        visibility_score=float(sum(visibility) / len(visibility)),
    )


def _midpoint(
    landmarks: tuple[PosePoint, ...], first: int, second: int, threshold: float
) -> Point | None:
    a, b = landmarks[first], landmarks[second]
    if min(a.visibility, b.visibility) < threshold:
        return None
    return ((a.x + b.x) / 2, (a.y + b.y) / 2)


def _joint_angle(
    landmarks: tuple[PosePoint, ...], a: int, b: int, c: int, threshold: float
) -> float | None:
    pa, pb, pc = landmarks[a], landmarks[b], landmarks[c]
    if min(pa.visibility, pb.visibility, pc.visibility) < threshold:
        return None
    first = np.array((pa.x - pb.x, pa.y - pb.y), dtype=float)
    second = np.array((pc.x - pb.x, pc.y - pb.y), dtype=float)
    denominator = np.linalg.norm(first) * np.linalg.norm(second)
    if denominator <= 1e-9:
        return None
    cosine = float(np.clip(np.dot(first, second) / denominator, -1.0, 1.0))
    return math.degrees(math.acos(cosine))
