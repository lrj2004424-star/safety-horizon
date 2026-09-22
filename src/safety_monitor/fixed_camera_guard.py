"""Detect camera displacement against a people-free fixed-station reference."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import FixedCameraConfig
from .geometry import normalized_to_pixels


@dataclass(frozen=True)
class CameraGuardObservation:
    stable: bool
    reason: str
    match_count: int
    translation_px: float
    rotation_deg: float
    scale: float
    quality: float


class FixedCameraGuard:
    """Estimate a partial affine transform using only configured static anchors."""

    def __init__(self, config: FixedCameraConfig, reference_image: str | Path | None):
        self.config = config
        self._frame_index = -1
        self._last = CameraGuardObservation(
            stable=not config.enabled,
            reason="fixed_camera_guard_disabled" if not config.enabled else "guard_warmup",
            match_count=0,
            translation_px=0.0,
            rotation_deg=0.0,
            scale=1.0,
            quality=0.0,
        )
        self._orb = cv2.ORB_create(nfeatures=1600, fastThreshold=7)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._reference_gray: np.ndarray | None = None
        self._reference_keypoints: tuple[cv2.KeyPoint, ...] = ()
        self._reference_descriptors: np.ndarray | None = None
        if not config.enabled:
            return
        if reference_image is None:
            raise ValueError("A people-free reference image is required when fixed camera guard is enabled")
        path = Path(reference_image)
        reference = cv2.imread(str(path))
        if reference is None:
            raise FileNotFoundError(f"Could not load fixed camera reference: {path}")
        self._reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
        mask = _anchor_mask(self._reference_gray.shape, config.anchor_rois)
        keypoints, descriptors = self._orb.detectAndCompute(self._reference_gray, mask)
        self._reference_keypoints = tuple(keypoints or ())
        self._reference_descriptors = descriptors
        if descriptors is None or len(self._reference_keypoints) < config.min_matches:
            raise ValueError(
                "Fixed camera reference has too few static features; choose larger textured anchor ROIs"
            )

    def update(self, frame_bgr: np.ndarray) -> CameraGuardObservation:
        if not self.config.enabled:
            return self._last
        self._frame_index += 1
        if (
            self._frame_index > 0
            and self._frame_index % self.config.check_interval_frames != 0
        ):
            return self._last
        assert self._reference_gray is not None
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if gray.shape != self._reference_gray.shape:
            self._last = _failed("camera_resolution_changed")
            return self._last
        mask = _anchor_mask(gray.shape, self.config.anchor_rois)
        keypoints, descriptors = self._orb.detectAndCompute(gray, mask)
        if descriptors is None or not keypoints or self._reference_descriptors is None:
            self._last = _failed("fixed_anchor_features_missing")
            return self._last
        pairs = self._matcher.knnMatch(self._reference_descriptors, descriptors, k=2)
        good = [first for pair in pairs if len(pair) == 2 for first, second in [pair] if first.distance < 0.75 * second.distance]
        if len(good) < self.config.min_matches:
            self._last = _failed("fixed_anchor_matches_insufficient", len(good))
            return self._last
        source = np.float32([self._reference_keypoints[item.queryIdx].pt for item in good])
        target = np.float32([keypoints[item.trainIdx].pt for item in good])
        transform, inliers = cv2.estimateAffinePartial2D(
            source,
            target,
            method=cv2.RANSAC,
            ransacReprojThreshold=2.5,
            maxIters=2000,
            confidence=0.995,
        )
        if transform is None:
            self._last = _failed("fixed_anchor_transform_failed", len(good))
            return self._last
        inlier_count = int(inliers.sum()) if inliers is not None else len(good)
        a, b = float(transform[0, 0]), float(transform[0, 1])
        tx, ty = float(transform[0, 2]), float(transform[1, 2])
        scale = math.hypot(a, b)
        rotation = math.degrees(math.atan2(float(transform[1, 0]), a))
        translation = math.hypot(tx, ty)
        stable = (
            inlier_count >= self.config.min_matches
            and translation <= self.config.max_translation_px
            and abs(rotation) <= self.config.max_rotation_deg
            and abs(scale - 1.0) <= self.config.max_scale_deviation
        )
        reason = "fixed_camera_alignment_ok" if stable else "fixed_camera_alignment_changed"
        quality = min(1.0, inlier_count / max(1.0, self.config.min_matches * 2.0))
        self._last = CameraGuardObservation(
            stable,
            reason,
            inlier_count,
            translation,
            rotation,
            scale,
            quality,
        )
        return self._last


def _failed(reason: str, matches: int = 0) -> CameraGuardObservation:
    return CameraGuardObservation(False, reason, matches, float("inf"), 0.0, 1.0, 0.0)


def _anchor_mask(
    shape: tuple[int, int], anchor_rois: tuple[tuple[tuple[float, float], ...], ...]
) -> np.ndarray:
    height, width = shape
    mask = np.zeros((height, width), dtype=np.uint8)
    for polygon in anchor_rois:
        points = np.array(normalized_to_pixels(polygon, width, height), np.int32)
        cv2.fillPoly(mask, [points], 255)
    return mask
