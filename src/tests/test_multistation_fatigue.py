import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from ezviz_multistation_fatigue_monitor import (
    StationResult,
    build_dashboard,
    build_pose_signal_analysis,
    build_risk_card,
    build_station_input_analysis,
    extract_video_frame,
    is_usable_video_frame,
    write_dashboard_jpeg,
)
from safety_monitor.fatigue_engine import FatigueAssessment, FatigueState
from safety_monitor.fatigue_status import (
    MultiStationFatigueStatusWriter,
    aggregate_fatigue_assessments,
)
from safety_monitor.fatigue_visualization import draw_station_fatigue_overlay
from safety_monitor.wide_station import load_wide_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def assessment(state: FatigueState, score: int, reason: str) -> FatigueAssessment:
    return FatigueAssessment(state, score, reason, (), None)


class MultiStationFatigueTests(unittest.TestCase):
    def test_loading_grey_surface_is_not_video(self):
        self.assertFalse(is_usable_video_frame(np.full((720, 1280, 3), 245, dtype=np.uint8)))

    def test_cyan_home_header_and_white_separator_are_not_video(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[:400] = (235, 195, 80)
        frame[400:450] = 230
        self.assertFalse(is_usable_video_frame(frame))

    def test_high_risk_is_not_hidden_by_other_station_uncertainty(self) -> None:
        state, score, reason = aggregate_fatigue_assessments(
            {
                "upper_station": assessment(FatigueState.HIGH_RISK, 84, "persistent"),
                "lower_station": assessment(FatigueState.UNCERTAIN, 0, "not_visible"),
            }
        )
        self.assertEqual(state, FatigueState.HIGH_RISK)
        self.assertEqual(score, 84)
        self.assertTrue(reason.startswith("upper_station:"))

    def test_uncertain_station_prevents_aggregate_active_claim(self) -> None:
        state, _, reason = aggregate_fatigue_assessments(
            {
                "upper_station": assessment(FatigueState.ACTIVE, 8, "baseline_ok"),
                "lower_station": assessment(FatigueState.UNCERTAIN, 0, "not_visible"),
            }
        )
        self.assertEqual(state, FatigueState.UNCERTAIN)
        self.assertTrue(reason.startswith("lower_station:"))

    def test_uncertain_status_remains_distinct_but_is_audibly_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            writer = MultiStationFatigueStatusWriter(path)
            writer.update(
                {
                    "upper_station": assessment(FatigueState.ACTIVE, 8, "active"),
                    "lower_station": assessment(FatigueState.UNCERTAIN, 0, "not_visible"),
                },
                1.0,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "UNCERTAIN")
            self.assertEqual(payload["state_code"], 2)
            self.assertEqual(payload["buzzer_level"], 0)

    def test_status_is_anonymous_and_contains_both_stations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            writer = MultiStationFatigueStatusWriter(path)
            writer.update(
                {
                    "upper_station": assessment(FatigueState.OBSERVING, 0, "baseline"),
                    "lower_station": assessment(FatigueState.ACTIVE, 12, "active"),
                },
                1.0,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 3)
            self.assertEqual(payload["state_code"], 1)
            self.assertEqual(payload["buzzer_level"], 0)
            self.assertEqual(payload["risk_score"], 0)
            self.assertEqual(len(payload["stations"]), 2)
            lower = next(
                station for station in payload["stations"]
                if station["station_id"] == "lower_station"
            )
            self.assertEqual(lower["state_code"], 0)
            self.assertEqual(lower["buzzer_level"], 0)
            self.assertFalse(payload["contains_identity"])
            self.assertFalse(payload["contains_image"])
            self.assertFalse(payload["medical_diagnosis"])

    def test_dashboard_places_two_independent_tiles_side_by_side(self) -> None:
        frame = np.full((240, 320, 3), 100, dtype=np.uint8)
        results = [
            StationResult(
                "upper_station",
                "STATION A - UPPER",
                frame,
                assessment(FatigueState.OBSERVING, 0, "baseline"),
            ),
            StationResult(
                "lower_station",
                "STATION B - LOWER",
                frame,
                assessment(FatigueState.ACTIVE, 9, "active"),
            ),
        ]
        dashboard, state, _, _ = build_dashboard(
            results,
            (1280, 720),
            recording=False,
            recording_mode=None,
        )
        self.assertEqual(dashboard.shape, (630, 1440, 3))
        self.assertEqual(state, FatigueState.OBSERVING)

    def test_single_station_dashboard_has_no_empty_right_tile(self) -> None:
        frame = np.full((240, 320, 3), 100, dtype=np.uint8)
        dashboard, state, _, _ = build_dashboard(
            [
                StationResult(
                    "upper_station",
                    "STATION A - LEFT",
                    frame,
                    assessment(FatigueState.OBSERVING, 0, "baseline"),
                )
            ],
            (1280, 720),
            recording=False,
            recording_mode=None,
        )
        self.assertEqual(dashboard.shape, (720, 1280, 3))
        self.assertEqual(state, FatigueState.OBSERVING)

    def test_single_station_dashboard_places_live_pixels_beside_telemetry(self) -> None:
        frame = np.full((360, 480, 3), (70, 90, 110), dtype=np.uint8)
        dashboard, _, _, _ = build_dashboard(
            [
                StationResult(
                    "upper_station",
                    "STATION A - LEFT",
                    frame,
                    assessment(FatigueState.ACTIVE, 18, "baseline_ok"),
                    frame,
                )
            ],
            (1280, 720),
            recording=False,
            recording_mode=None,
            risk_history=[2, 5, 9, 18],
        )
        self.assertEqual(dashboard.shape, (720, 1280, 3))
        self.assertGreater(float(dashboard[100:680, 30:800].mean()), 30.0)
        self.assertNotAlmostEqual(
            float(dashboard[100:680, 30:800].mean()),
            float(dashboard[100:680, 830:1250].mean()),
            delta=2.0,
        )

    def test_risk_card_is_a_non_video_decision_stage(self) -> None:
        card = build_risk_card(FatigueState.HIGH_RISK, 82, "upper_station:persistent_fatigue")
        self.assertEqual(card.shape, (720, 1280, 3))
        self.assertGreater(float(card.mean()), 5.0)

    def test_station_input_stage_fills_wide_canvas_with_roi_analysis(self) -> None:
        source = np.full((720, 1280, 3), (70, 95, 120), dtype=np.uint8)
        manifest = load_wide_manifest(PROJECT_ROOT / "config" / "ezviz-fatigue-single-upper.json")
        crop = np.full((548, 614, 3), (80, 105, 130), dtype=np.uint8)
        result = StationResult(
            "upper_station",
            "STATION A - LEFT",
            crop,
            assessment(FatigueState.ACTIVE, 12, "baseline_ok"),
            crop,
            crop,
        )
        rendered = build_station_input_analysis(source, manifest.stations[0], result)
        self.assertEqual(rendered.shape, (720, 1280, 3))
        self.assertGreater(float(rendered[:, :80].mean()), 8.0)
        self.assertGreater(float(rendered[:, -80:].mean()), 8.0)

    def test_pose_signal_stage_uses_side_space_for_live_metrics(self) -> None:
        frame = np.full((548, 614, 3), (70, 90, 110), dtype=np.uint8)
        rendered = build_pose_signal_analysis(
            StationResult(
                "upper_station",
                "STATION A - LEFT",
                frame,
                assessment(FatigueState.UNCERTAIN, 0, "no_reliable_whole_person_pose"),
                frame,
                frame,
            )
        )
        self.assertEqual(rendered.shape, (720, 1280, 3))
        self.assertGreater(float(rendered[:, -240:].mean()), 8.0)
        self.assertGreater(float(rendered[80:686, 734:].std()), 5.0)

    def test_station_header_is_outside_video_instead_of_covering_person(self) -> None:
        frame = np.full((240, 320, 3), 100, dtype=np.uint8)
        rendered = draw_station_fatigue_overlay(
            frame,
            assessment(FatigueState.ACTIVE, 5, "active"),
            "STATION A - UPPER",
        )
        self.assertEqual(rendered.shape, (344, 320, 3))
        self.assertAlmostEqual(float(rendered[104:].mean()), 100.0, delta=3.0)

    def test_ezviz_manifest_has_two_verified_non_overlapping_station_centers(self) -> None:
        manifest = load_wide_manifest(
            PROJECT_ROOT / "config" / "ezviz-fatigue-multistation.json"
        )
        self.assertEqual([item.station_id for item in manifest.stations], ["upper_station", "lower_station"])
        self.assertTrue(all(item.crop_verified for item in manifest.stations))
        upper, lower = manifest.stations
        upper_center_y = (upper.crop[1] + upper.crop[3]) / 2
        lower_center_y = (lower.crop[1] + lower.crop[3]) / 2
        self.assertLess(upper_center_y, lower_center_y)

    def test_portrait_app_automatically_extracts_top_video_player(self) -> None:
        portrait = np.zeros((862, 464, 3), dtype=np.uint8)
        portrait[30:297] = 180
        extracted = extract_video_frame(portrait, "auto")
        self.assertEqual(extracted.shape, (720, 1280, 3))
        self.assertGreater(float(extracted.mean()), 170.0)

    def test_portrait_extraction_excludes_player_overlay_bands(self) -> None:
        portrait = np.zeros((862, 464, 3), dtype=np.uint8)
        portrait[30:297] = (180, 180, 180)
        portrait[30:55] = (0, 0, 255)   # timestamp/back-arrow band
        portrait[292:297] = (255, 0, 0) # playback progress line
        extracted = extract_video_frame(portrait, "auto")
        self.assertLess(float(extracted[:, :, 0].max()), 200.0)
        self.assertLess(float(extracted[:, :, 2].max()), 200.0)

    def test_blank_lock_screen_frame_is_not_treated_as_live_video(self) -> None:
        self.assertFalse(is_usable_video_frame(np.zeros((720, 1280, 3), dtype=np.uint8)))
        textured = np.indices((720, 1280)).sum(axis=0).astype(np.uint8)
        frame = np.dstack((textured, textured, textured))
        self.assertTrue(is_usable_video_frame(frame))

    def test_touchdesigner_dashboard_output_is_a_complete_jpeg(self) -> None:
        """The external TD viewer never reads a half-written frame."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "touchdesigner-dashboard.jpg"
            dashboard = np.full((630, 1440, 3), 96, dtype=np.uint8)
            write_dashboard_jpeg(path, dashboard)
            decoded = cv2.imread(str(path), cv2.IMREAD_COLOR)
            self.assertIsNotNone(decoded)
            assert decoded is not None
            self.assertEqual(decoded.shape, dashboard.shape)
            self.assertFalse(path.with_suffix(".jpg.tmp").exists())


if __name__ == "__main__":
    unittest.main()
