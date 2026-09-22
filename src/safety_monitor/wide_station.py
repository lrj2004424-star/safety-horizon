"""Validated single-wide-camera layout for independent workstation pipelines."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


NormalizedRect = tuple[float, float, float, float]


@dataclass(frozen=True)
class WideStationSpec:
    station_id: str
    config_path: Path
    crop: NormalizedRect
    enabled: bool
    crop_verified: bool
    zones_calibrated: bool
    min_crop_width_px: int
    min_crop_height_px: int
    pose_interval_frames: int


@dataclass(frozen=True)
class WideCameraManifest:
    path: Path
    schema_version: int
    min_source_width_px: int
    min_source_height_px: int
    stations: tuple[WideStationSpec, ...]


def load_wide_manifest(path: str | Path) -> WideCameraManifest:
    manifest_path = Path(path).expanduser().resolve()
    raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Wide-camera manifest must be a JSON object")
    schema_version = int(raw.get("schema_version", 1))
    if schema_version != 1:
        raise ValueError("Unsupported wide-camera manifest schema_version")
    source = raw.get("source_requirements", {})
    if not isinstance(source, dict):
        raise ValueError("source_requirements must be an object")
    min_source_width = _positive_int(
        source.get("min_width_px", 1280), "source_requirements.min_width_px"
    )
    min_source_height = _positive_int(
        source.get("min_height_px", 720), "source_requirements.min_height_px"
    )
    station_values = raw.get("stations")
    if not isinstance(station_values, list) or not station_values:
        raise ValueError("stations must contain at least one workstation")
    stations: list[WideStationSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(station_values):
        if not isinstance(item, dict):
            raise ValueError(f"stations[{index}] must be an object")
        station_id = str(item.get("station_id", "")).strip()
        if not station_id:
            raise ValueError(f"stations[{index}].station_id is required")
        if station_id in seen:
            raise ValueError(f"Duplicate station_id: {station_id}")
        seen.add(station_id)
        config_value = str(item.get("config", "")).strip()
        if not config_value:
            raise ValueError(f"stations[{index}].config is required")
        config_path = Path(config_value).expanduser()
        if not config_path.is_absolute():
            config_path = (manifest_path.parent / config_path).resolve()
        crop = _normalized_rect(item.get("crop"), f"stations[{index}].crop")
        stations.append(
            WideStationSpec(
                station_id=station_id,
                config_path=config_path,
                crop=crop,
                enabled=bool(item.get("enabled", True)),
                crop_verified=bool(item.get("crop_verified", False)),
                zones_calibrated=bool(item.get("zones_calibrated", False)),
                min_crop_width_px=_positive_int(
                    item.get("min_crop_width_px", 480),
                    f"stations[{index}].min_crop_width_px",
                ),
                min_crop_height_px=_positive_int(
                    item.get("min_crop_height_px", 320),
                    f"stations[{index}].min_crop_height_px",
                ),
                pose_interval_frames=_positive_int(
                    item.get("pose_interval_frames", 1),
                    f"stations[{index}].pose_interval_frames",
                ),
            )
        )
    enabled = tuple(station for station in stations if station.enabled)
    if not enabled:
        raise ValueError("At least one station must be enabled")
    return WideCameraManifest(
        path=manifest_path,
        schema_version=schema_version,
        min_source_width_px=min_source_width,
        min_source_height_px=min_source_height,
        stations=enabled,
    )


def crop_bounds(
    crop: NormalizedRect,
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int]:
    if frame_width < 2 or frame_height < 2:
        raise ValueError("Frame dimensions must be at least 2x2")
    x0, y0, x1, y1 = crop
    left = max(0, min(frame_width - 1, int(math.floor(x0 * frame_width))))
    top = max(0, min(frame_height - 1, int(math.floor(y0 * frame_height))))
    right = max(left + 1, min(frame_width, int(math.ceil(x1 * frame_width))))
    bottom = max(top + 1, min(frame_height, int(math.ceil(y1 * frame_height))))
    return left, top, right, bottom


def crop_frame(frame: np.ndarray, crop: NormalizedRect) -> np.ndarray:
    if frame is None or frame.ndim != 3:
        raise ValueError("Expected a BGR color frame")
    height, width = frame.shape[:2]
    left, top, right, bottom = crop_bounds(crop, width, height)
    return frame[top:bottom, left:right].copy()


def update_manifest_station(
    path: str | Path,
    station_id: str,
    *,
    crop: NormalizedRect | None = None,
    crop_verified: bool | None = None,
    zones_calibrated: bool | None = None,
) -> None:
    """Atomically update calibration flags while preserving unknown manifest fields."""
    manifest_path = Path(path).expanduser().resolve()
    raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("stations"), list):
        raise ValueError("Invalid wide-camera manifest")
    matched = False
    for item in raw["stations"]:
        if isinstance(item, dict) and str(item.get("station_id", "")) == station_id:
            if crop is not None:
                item["crop"] = [round(float(value), 6) for value in crop]
            if crop_verified is not None:
                item["crop_verified"] = bool(crop_verified)
            if zones_calibrated is not None:
                item["zones_calibrated"] = bool(zones_calibrated)
            matched = True
            break
    if not matched:
        raise ValueError(f"Unknown station_id: {station_id}")
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    load_wide_manifest(manifest_path)


def station_quality_issues(
    manifest: WideCameraManifest,
    station: WideStationSpec,
    source_width: int,
    source_height: int,
) -> tuple[str, ...]:
    left, top, right, bottom = crop_bounds(
        station.crop, source_width, source_height
    )
    issues: list[str] = []
    if source_width < manifest.min_source_width_px or source_height < manifest.min_source_height_px:
        issues.append("source_resolution_below_manifest_minimum")
    if not station.crop_verified:
        issues.append("station_crop_not_verified")
    if not station.zones_calibrated:
        issues.append("station_zones_not_calibrated")
    if right - left < station.min_crop_width_px or bottom - top < station.min_crop_height_px:
        issues.append("station_crop_resolution_too_low")
    return tuple(issues)


def _positive_int(value: Any, name: str) -> int:
    result = int(value)
    if result < 1:
        raise ValueError(f"{name} must be positive")
    return result


def _normalized_rect(value: Any, name: str) -> NormalizedRect:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{name} must be [left, top, right, bottom]")
    left, top, right, bottom = (float(item) for item in value)
    if not all(0.0 <= item <= 1.0 for item in (left, top, right, bottom)):
        raise ValueError(f"{name} must use normalized values from 0 to 1")
    if right - left < 0.05 or bottom - top < 0.05:
        raise ValueError(f"{name} is too small or has reversed edges")
    return left, top, right, bottom
