import unittest

from safety_monitor.fatigue_engine import FatigueRiskEngine, FatigueState
from safety_monitor.pose_tracker import PosePoint, build_pose_observation


def pose(
    *,
    center_x: float = 0.5,
    head_y: float = 0.20,
    shoulder_shift_x: float = 0.0,
    shoulder_tilt_y: float = 0.0,
    motion_offset: float = 0.0,
):
    points = [PosePoint(center_x + motion_offset, 0.5, 1.0, 1.0) for _ in range(33)]
    left_shoulder_x = center_x - 0.10 + shoulder_shift_x + motion_offset
    right_shoulder_x = center_x + 0.10 + shoulder_shift_x + motion_offset
    for index in range(11):
        points[index] = PosePoint(
            center_x + shoulder_shift_x + motion_offset,
            head_y,
            1.0,
            1.0,
        )
    points[11] = PosePoint(left_shoulder_x, 0.30 + shoulder_tilt_y, 1.0, 1.0)
    points[12] = PosePoint(right_shoulder_x, 0.30, 1.0, 1.0)
    points[13] = PosePoint(left_shoulder_x, 0.47, 1.0, 1.0)
    points[14] = PosePoint(right_shoulder_x, 0.47, 1.0, 1.0)
    points[15] = PosePoint(left_shoulder_x, 0.62, 1.0, 1.0)
    points[16] = PosePoint(right_shoulder_x, 0.62, 1.0, 1.0)
    points[23] = PosePoint(center_x - 0.08 + motion_offset, 0.70, 1.0, 1.0)
    points[24] = PosePoint(center_x + 0.08 + motion_offset, 0.70, 1.0, 1.0)
    points[25] = PosePoint(center_x - 0.08 + motion_offset, 0.82, 1.0, 1.0)
    points[26] = PosePoint(center_x + 0.08 + motion_offset, 0.82, 1.0, 1.0)
    points[27] = PosePoint(center_x - 0.08 + motion_offset, 0.95, 1.0, 1.0)
    points[28] = PosePoint(center_x + 0.08 + motion_offset, 0.95, 1.0, 1.0)
    return build_pose_observation(tuple(points))


class FatigueEngineTests(unittest.TestCase):
    def engine(self) -> FatigueRiskEngine:
        return FatigueRiskEngine(
            baseline_seconds=1.0,
            trend_seconds=1.0,
            high_risk_seconds=2.0,
            missing_reset_seconds=2.0,
        )

    def establish_baseline(self, engine: FatigueRiskEngine, center_x: float = 0.5) -> None:
        for step in range(11):
            result = engine.update([pose(center_x=center_x, motion_offset=(step % 2) * 0.002)], step * 0.1)
        self.assertEqual(result.state, FatigueState.OBSERVING)

    def test_baseline_then_normal_work_is_active(self) -> None:
        engine = self.engine()
        self.establish_baseline(engine)
        result = engine.update([pose(motion_offset=0.004)], 1.2)
        self.assertEqual(result.state, FatigueState.ACTIVE)
        self.assertEqual(result.persons[0].posture_signal_count, 0)

    def test_persistent_combined_posture_change_reaches_high_risk(self) -> None:
        engine = self.engine()
        self.establish_baseline(engine)
        altered = pose(head_y=0.32, shoulder_shift_x=0.13, shoulder_tilt_y=0.08)
        result = None
        for step in range(1, 25):
            result = engine.update([altered], 1.0 + step * 0.1)
        assert result is not None
        self.assertEqual(result.state, FatigueState.HIGH_RISK)
        self.assertGreaterEqual(result.persons[0].posture_signal_count, 2)
        self.assertGreaterEqual(result.score, 70)

    def test_fatigue_score_bands_do_not_overlap(self) -> None:
        engine = self.engine()
        self.establish_baseline(engine)
        altered = pose(head_y=0.32, shoulder_shift_x=0.13, shoulder_tilt_y=0.08)
        trend = None
        for step in range(1, 14):
            trend = engine.update([altered], 1.0 + step * 0.1)
        assert trend is not None
        self.assertEqual(trend.state, FatigueState.FATIGUE_TREND)
        self.assertGreaterEqual(trend.score, 45)
        self.assertLessEqual(trend.score, 69)

    def test_low_motion_alone_never_becomes_high_risk(self) -> None:
        engine = self.engine()
        self.establish_baseline(engine)
        result = None
        for step in range(1, 40):
            result = engine.update([pose()], 1.0 + step * 0.1)
        assert result is not None
        self.assertEqual(result.state, FatigueState.ACTIVE)

    def test_two_people_are_anonymous_separate_tracks(self) -> None:
        engine = self.engine()
        for step in range(11):
            result = engine.update([pose(center_x=0.3), pose(center_x=0.7)], step * 0.1)
        result = engine.update([pose(center_x=0.3), pose(center_x=0.7)], 1.2)
        self.assertEqual(len(result.persons), 2)
        self.assertEqual({person.track_id for person in result.persons}, {1, 2})

    def test_worker_movement_does_not_reset_personal_baseline(self) -> None:
        engine = self.engine()
        self.establish_baseline(engine, center_x=0.25)
        moved = engine.update([pose(center_x=0.55)], 1.2)
        self.assertEqual(moved.persons[0].track_id, 1)
        self.assertEqual(moved.persons[0].state, FatigueState.ACTIVE)

    def test_alternating_distant_workers_get_stable_spatial_tracks(self) -> None:
        engine = self.engine()
        first = engine.update([pose(center_x=0.25)], 0.0)
        second = engine.update([pose(center_x=0.65)], 0.1)
        again_first = engine.update([pose(center_x=0.25)], 0.2)
        again_second = engine.update([pose(center_x=0.65)], 0.3)
        self.assertEqual(first.persons[0].track_id, 1)
        self.assertEqual(second.persons[-1].track_id, 2)
        self.assertEqual(again_first.persons[0].track_id, 1)
        self.assertEqual(again_second.persons[-1].track_id, 2)

    def test_implausible_inverted_torso_is_not_used(self) -> None:
        invalid = pose()
        points = list(invalid.landmarks)
        points[23] = PosePoint(0.42, 0.20, 1.0, 1.0)
        points[24] = PosePoint(0.58, 0.20, 1.0, 1.0)
        invalid = build_pose_observation(tuple(points))
        result = self.engine().update([invalid], 0.0)
        self.assertEqual(result.state, FatigueState.UNCERTAIN)

    def test_nearly_horizontal_torso_is_filtered_as_perspective_error(self) -> None:
        invalid = pose(shoulder_shift_x=0.85)
        result = self.engine().update([invalid], 0.0)
        self.assertEqual(result.state, FatigueState.UNCERTAIN)

    def test_visible_ears_allow_back_facing_worker_without_nose(self) -> None:
        back_facing = pose()
        points = list(back_facing.landmarks)
        points[0] = PosePoint(points[0].x, points[0].y, 0.05, 1.0)
        points[7] = PosePoint(0.46, 0.21, 0.8, 1.0)
        points[8] = PosePoint(0.54, 0.21, 0.8, 1.0)
        back_facing = build_pose_observation(tuple(points), min_visibility=0.30)
        result = self.engine().update([back_facing], 0.0)
        self.assertEqual(result.state, FatigueState.OBSERVING)

    def test_station_quality_gate_rejects_sparse_small_pose(self) -> None:
        sparse = pose()
        points = list(sparse.landmarks)
        for index in (13, 14, 15, 16, 25, 26, 27, 28):
            points[index] = PosePoint(points[index].x, points[index].y, 0.05, 1.0)
        sparse = build_pose_observation(tuple(points), min_visibility=0.30)
        engine = FatigueRiskEngine(
            baseline_seconds=1.0,
            trend_seconds=1.0,
            high_risk_seconds=2.0,
            min_body_visibility=0.30,
            min_body_coverage=0.60,
            min_body_span=0.38,
            min_torso_length=0.08,
        )
        result = engine.update([sparse], 0.0)
        self.assertEqual(result.state, FatigueState.UNCERTAIN)

    def test_station_quality_gate_rejects_inverted_lower_body(self) -> None:
        invalid = pose()
        points = list(invalid.landmarks)
        points[25] = PosePoint(0.42, 0.55, 1.0, 1.0)
        points[26] = PosePoint(0.58, 0.55, 1.0, 1.0)
        invalid = build_pose_observation(tuple(points), min_visibility=0.30)
        engine = FatigueRiskEngine(
            baseline_seconds=1.0,
            trend_seconds=1.0,
            high_risk_seconds=2.0,
            min_body_visibility=0.30,
            min_body_coverage=0.60,
            min_body_span=0.38,
            min_torso_length=0.08,
            require_lower_body_order=True,
        )
        self.assertEqual(engine.update([invalid], 0.0).state, FatigueState.UNCERTAIN)

    def test_track_requires_repeated_confirmation_when_configured(self) -> None:
        engine = FatigueRiskEngine(
            baseline_seconds=1.0,
            trend_seconds=1.0,
            high_risk_seconds=2.0,
            min_track_confirmations=3,
        )
        self.assertEqual(engine.update([pose()], 0.0).state, FatigueState.UNCERTAIN)
        self.assertEqual(engine.update([pose()], 0.1).state, FatigueState.UNCERTAIN)
        self.assertEqual(engine.update([pose()], 0.2).state, FatigueState.OBSERVING)

    def test_missing_pose_is_uncertain_not_safe(self) -> None:
        engine = self.engine()
        self.establish_baseline(engine)
        brief_gap = engine.update([], 1.2)
        self.assertEqual(brief_gap.state, FatigueState.OBSERVING)
        result = engine.update([], 3.2)
        self.assertEqual(result.state, FatigueState.UNCERTAIN)
        self.assertEqual(result.reason, "no_reliable_whole_person_pose")


if __name__ == "__main__":
    unittest.main()
