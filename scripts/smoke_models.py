"""Actual model inference on synthetic blank input / 真实加载模型，不使用员工素材。"""
from pathlib import Path
import json
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

root = Path(__file__).resolve().parents[1] / "src/models"
image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((256, 256, 3), dtype=np.uint8))
result = []
for filename in ("pose_landmarker_full.task", "pose_landmarker_lite.task"):
    options = vision.PoseLandmarkerOptions(base_options=BaseOptions(
        model_asset_path=str(root / filename), delegate=BaseOptions.Delegate.CPU))
    with vision.PoseLandmarker.create_from_options(options) as detector:
        output = detector.detect(image)
        assert len(output.pose_landmarks) == 0, "Blank frame must not create synthetic people"
        result.append({"model": filename, "load_and_inference": "PASS", "blank_detections": 0})
options = vision.HandLandmarkerOptions(base_options=BaseOptions(
    model_asset_path=str(root / "hand_landmarker.task"), delegate=BaseOptions.Delegate.CPU))
with vision.HandLandmarker.create_from_options(options) as detector:
    output = detector.detect(image)
    assert len(output.hand_landmarks) == 0
    result.append({"model": "hand_landmarker.task", "load_and_inference": "PASS", "blank_detections": 0})
print(json.dumps(result, indent=2))
