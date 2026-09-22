"""Privacy transformations for derived demonstration outputs."""

from __future__ import annotations

import cv2
import numpy as np

from .pose_tracker import PoseObservation


def blur_face_from_pose(frame: np.ndarray, pose: PoseObservation | None) -> np.ndarray:
    """Blur a conservative head box derived from pose landmarks 0..10."""
    if pose is None or len(pose.landmarks) < 11:
        return frame
    visible = [point for point in pose.landmarks[:11] if point.visibility >= 0.25]
    if len(visible) < 3:
        return frame
    height, width = frame.shape[:2]
    xs = [point.x * width for point in visible]
    ys = [point.y * height for point in visible]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    size = max(x2 - x1, y2 - y1, width * 0.055)
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    left = max(0, int(center_x - size * 0.9))
    right = min(width, int(center_x + size * 0.9))
    top = max(0, int(center_y - size * 0.9))
    bottom = min(height, int(center_y + size * 1.15))
    if right <= left or bottom <= top:
        return frame
    roi = frame[top:bottom, left:right]
    kernel = max(31, (min(roi.shape[:2]) // 3) | 1)
    frame[top:bottom, left:right] = cv2.GaussianBlur(roi, (kernel, kernel), 0)
    return frame
