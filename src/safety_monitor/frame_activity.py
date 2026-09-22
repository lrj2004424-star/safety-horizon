"""Conservative evidence of unchanged pixels, not proof of camera liveness."""
import math
import cv2
import numpy as np


class FrameActivityGuard:
    def __init__(self, timeout_s=12.0, minimum_change=0.25):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        if not math.isfinite(minimum_change) or minimum_change < 0:
            raise ValueError("minimum_change must be finite and nonnegative")
        self.timeout_s, self.minimum_change = timeout_s, minimum_change
        self.reset()

    def reset(self):
        self.reference = None
        self.last_change = None
        self.last_time = None
        self.context = None

    def observe(self, frame, now_s, *, context="LIVE"):
        if not math.isfinite(now_s):
            raise ValueError("timestamp must be finite")
        small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA).astype(np.int16)
        reset = self.reference is None or self.context != context or now_s < self.last_time
        if reset or float(np.abs(small - self.reference).mean()) > self.minimum_change:
            # Compare to the last meaningfully changed image, not the previous
            # frame: slow cumulative changes must eventually count as activity.
            self.reference = small
            self.last_change = now_s
        self.context, self.last_time = context, now_s
        return now_s - self.last_change < self.timeout_s
