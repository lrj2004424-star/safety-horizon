"""Experimental optical-flow estimate for a calibrated machine motion ROI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from .config import MachineConfig
from .geometry import normalized_to_pixels


class MachineState(str, Enum):
    STATIC = "STATIC"
    PRESSING = "PRESSING"
    RISING = "RISING"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class MachineObservation:
    state: MachineState
    raw_state: MachineState
    vertical_flow: float
    moving_fraction: float
    reference_motion: float
    quality_ok: bool
    reason: str


class MachineMotionEstimator:
    """Estimate vertical motion after subtracting a static reference ROI.

    This is explicitly not a direct press-state sensor. Hands, material and tools
    inside the ROI can contaminate the result; quality gates must then return
    UNCERTAIN rather than a safety claim.
    """

    def __init__(self, config: MachineConfig):
        self.config = config
        self._previous_gray: np.ndarray | None = None
        self._state = MachineState.UNCERTAIN
        self._candidate: MachineState | None = None
        self._candidate_frames = 0
        self._smoothed_vertical = 0.0

    def update(
        self,
        frame_bgr: np.ndarray,
        exclusion_points: list[tuple[float, float]] | None = None,
    ) -> MachineObservation:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if self._previous_gray is None or self._previous_gray.shape != gray.shape:
            self._previous_gray = gray
            return MachineObservation(
                MachineState.UNCERTAIN,
                MachineState.UNCERTAIN,
                0.0,
                0.0,
                0.0,
                False,
                "machine_motion_warmup",
            )
        flow = cv2.calcOpticalFlowFarneback(
            self._previous_gray,
            gray,
            None,
            0.5,
            3,
            19,
            3,
            5,
            1.1,
            0,
        )
        self._previous_gray = gray
        motion_mask = _polygon_mask(gray.shape, self.config.motion_roi)
        base_motion_pixels = int(motion_mask.sum())
        if exclusion_points:
            exclusion_mask = np.zeros(gray.shape, dtype=np.uint8)
            height, width = gray.shape
            for x, y in exclusion_points:
                cv2.circle(
                    exclusion_mask,
                    (int(x * width), int(y * height)),
                    self.config.exclusion_radius_px,
                    1,
                    -1,
                )
            motion_mask &= exclusion_mask == 0
        excluded_fraction = (
            1.0 - float(motion_mask.sum()) / base_motion_pixels
            if base_motion_pixels else 1.0
        )
        reference_mask = _polygon_mask(gray.shape, self.config.reference_roi)
        motion_vectors = flow[motion_mask]
        reference_vectors = flow[reference_mask]
        if excluded_fraction > self.config.max_excluded_fraction:
            return self._observation(
                MachineState.UNCERTAIN,
                0.0,
                0.0,
                0.0,
                False,
                "hand_occludes_machine_roi",
            )
        if motion_vectors.size == 0 or reference_vectors.size == 0:
            return self._observation(MachineState.UNCERTAIN, 0.0, 0.0, 0.0, False, "invalid_machine_roi")

        reference_motion = float(np.median(np.linalg.norm(reference_vectors, axis=1)))
        corrected_vertical = motion_vectors[:, 1] - float(np.median(reference_vectors[:, 1]))
        magnitudes = np.linalg.norm(motion_vectors, axis=1)
        moving = magnitudes > self.config.vertical_motion_threshold
        moving_fraction = float(moving.mean())
        vertical = float(np.median(corrected_vertical[moving])) if moving.any() else 0.0
        directional = corrected_vertical[moving]
        directional_coherence = 0.0
        if directional.size:
            median_sign = np.sign(float(np.median(directional)))
            directional_coherence = (
                float(np.mean(np.sign(directional) == median_sign))
                if median_sign else 0.0
            )
        alpha = self.config.smoothing
        self._smoothed_vertical = alpha * vertical + (1.0 - alpha) * self._smoothed_vertical

        if reference_motion > self.config.global_motion_threshold:
            raw = MachineState.UNCERTAIN
            quality_ok = False
            reason = "camera_or_global_motion"
        elif moving_fraction < self.config.min_motion_pixels:
            raw = MachineState.STATIC
            quality_ok = True
            reason = "machine_roi_static"
        elif abs(self._smoothed_vertical) < self.config.vertical_motion_threshold:
            raw = MachineState.UNCERTAIN
            quality_ok = False
            reason = "mixed_or_horizontal_roi_motion"
        elif directional_coherence < self.config.min_directional_coherence:
            raw = MachineState.UNCERTAIN
            quality_ok = False
            reason = "incoherent_roi_motion"
        elif self._smoothed_vertical > 0:
            raw = MachineState.PRESSING
            quality_ok = True
            reason = "downward_roi_flow"
        else:
            raw = MachineState.RISING
            quality_ok = True
            reason = "upward_roi_flow"
        if not self.config.calibrated_with_empty_cycles:
            self._state = MachineState.UNCERTAIN
            self._candidate = None
            self._candidate_frames = 0
            return MachineObservation(
                state=MachineState.UNCERTAIN,
                raw_state=raw,
                vertical_flow=self._smoothed_vertical,
                moving_fraction=moving_fraction,
                reference_motion=reference_motion,
                quality_ok=False,
                reason=f"uncalibrated_roi_visual_cue_{raw.value.lower()}",
            )
        return self._observation(
            raw,
            self._smoothed_vertical,
            moving_fraction,
            reference_motion,
            quality_ok,
            reason,
        )

    def _observation(
        self,
        raw: MachineState,
        vertical: float,
        moving_fraction: float,
        reference_motion: float,
        quality_ok: bool,
        reason: str,
    ) -> MachineObservation:
        if raw == self._state:
            self._candidate = None
            self._candidate_frames = 0
        else:
            if raw != self._candidate:
                self._candidate = raw
                self._candidate_frames = 1
            else:
                self._candidate_frames += 1
            threshold = 1 if raw == MachineState.UNCERTAIN else self.config.direction_hold_frames
            if self._candidate_frames >= threshold:
                self._state = raw
                self._candidate = None
                self._candidate_frames = 0
        return MachineObservation(
            self._state,
            raw,
            vertical,
            moving_fraction,
            reference_motion,
            quality_ok,
            reason,
        )


def _polygon_mask(shape: tuple[int, int], polygon: tuple[tuple[float, float], ...]) -> np.ndarray:
    height, width = shape
    points = np.array(normalized_to_pixels(polygon, width, height), np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [points], 1)
    return mask.astype(bool)
