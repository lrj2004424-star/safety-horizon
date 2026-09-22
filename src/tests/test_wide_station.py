import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from safety_monitor.wide_station import (
    crop_bounds,
    crop_frame,
    load_wide_manifest,
    station_quality_issues,
    update_manifest_station,
)


def manifest_payload() -> dict:
    return {
        "schema_version": 1,
        "source_requirements": {"min_width_px": 1920, "min_height_px": 1080},
        "stations": [
            {
                "station_id": "upper",
                "config": "upper.json",
                "crop": [0.25, 0.1, 0.75, 0.8],
                "enabled": True,
                "crop_verified": False,
                "zones_calibrated": False,
                "min_crop_width_px": 600,
                "min_crop_height_px": 400,
            }
        ],
    }


class WideStationTests(unittest.TestCase):
    def test_manifest_crop_and_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wide.json"
            path.write_text(json.dumps(manifest_payload()), encoding="utf-8")
            manifest = load_wide_manifest(path)
            station = manifest.stations[0]
            self.assertEqual(crop_bounds(station.crop, 2000, 1200), (500, 120, 1500, 960))
            frame = np.zeros((1200, 2000, 3), dtype=np.uint8)
            self.assertEqual(crop_frame(frame, station.crop).shape, (840, 1000, 3))
            self.assertEqual(
                station_quality_issues(manifest, station, 2000, 1200),
                ("station_crop_not_verified", "station_zones_not_calibrated"),
            )

    def test_low_source_or_crop_resolution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wide.json"
            path.write_text(json.dumps(manifest_payload()), encoding="utf-8")
            manifest = load_wide_manifest(path)
            issues = station_quality_issues(
                manifest, manifest.stations[0], 900, 500
            )
            self.assertIn("source_resolution_below_manifest_minimum", issues)
            self.assertIn("station_crop_resolution_too_low", issues)

    def test_calibration_flags_update_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wide.json"
            path.write_text(json.dumps(manifest_payload()), encoding="utf-8")
            update_manifest_station(
                path,
                "upper",
                crop=(0.1, 0.2, 0.8, 0.9),
                crop_verified=True,
                zones_calibrated=True,
            )
            station = load_wide_manifest(path).stations[0]
            self.assertEqual(station.crop, (0.1, 0.2, 0.8, 0.9))
            self.assertTrue(station.crop_verified)
            self.assertTrue(station.zones_calibrated)

    def test_duplicate_station_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = manifest_payload()
            payload["stations"].append(dict(payload["stations"][0]))
            path = Path(directory) / "wide.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate station_id"):
                load_wide_manifest(path)

if __name__ == "__main__":
    unittest.main()
