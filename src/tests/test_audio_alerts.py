import tempfile
import sys
import unittest
import wave
from array import array
from pathlib import Path

from safety_monitor.audio_alerts import (
    ASSET_FILES,
    RISK_SOUND_FILES,
    SYSTEM_FALLBACK_FILE,
    AlertSoundManager,
    system_sentence,
)
from safety_monitor.config import AudioAlertConfig
from safety_monitor.joint_engine import JointState


def config(*, voice: bool = True) -> AudioAlertConfig:
    return AudioAlertConfig(
        enabled=True,
        live_only=True,
        assets_dir="unused",
        volume=0.7,
        startup_grace_s=1.0,
        system_voice_enabled=voice,
        system_voice_name="Tingting",
        system_voice_rate=210,
        attention_repeat_s=15.0,
        warning_repeat_s=12.0,
        danger_repeat_s=10.0,
        uncertain_repeat_s=15.0,
        fault_repeat_s=10.0,
    )


def make_assets(root: Path) -> None:
    for filename in ASSET_FILES:
        (root / filename).touch()


class AudioAlertTests(unittest.TestCase):
    def test_normal_is_silent_and_risk_cue_obeys_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_assets(root)
            played: list[str] = []
            manager = AlertSoundManager(
                config(),
                root,
                player=lambda path, _volume: played.append(path.name) is None,
                speaker=lambda _message, _voice, _rate: True,
            )
            self.assertFalse(manager.update(JointState.NORMAL, 0.0).played)
            self.assertFalse(manager.update(JointState.ATTENTION, 0.5).played)
            self.assertTrue(manager.update(JointState.ATTENTION, 1.0).played)
            self.assertFalse(manager.update(JointState.ATTENTION, 3.0).played)
            self.assertTrue(manager.update(JointState.ATTENTION, 16.1).played)
            self.assertFalse(manager.update(JointState.NORMAL, 16.2).played)
            self.assertEqual(
                played,
                [
                    "01-yellow-attention-2s-68bpm.wav",
                    "01-yellow-attention-2s-68bpm.wav",
                ],
            )

    def test_three_risk_levels_use_one_pulse_family_with_duration_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_assets(root)
            played: list[str] = []
            manager = AlertSoundManager(
                config(),
                root,
                player=lambda path, _volume: played.append(path.name) is None,
                speaker=lambda _message, _voice, _rate: True,
            )
            for index, state in enumerate(
                (JointState.ATTENTION, JointState.WARNING, JointState.DANGER),
                start=2,
            ):
                self.assertTrue(manager.update(state, float(index)).played)
            self.assertEqual(played, list(RISK_SOUND_FILES.values()))

    def test_system_states_speak_one_reason_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_assets(root)
            spoken: list[str] = []
            manager = AlertSoundManager(
                config(),
                root,
                player=lambda _path, _volume: True,
                speaker=lambda message, _voice, _rate: spoken.append(message) is None,
            )
            first = manager.update(
                JointState.UNCERTAIN,
                2.0,
                reason="hand_or_upper_body_not_reliably_visible",
            )
            changed = manager.update(
                JointState.UNCERTAIN,
                5.1,
                reason="machine_state_machine_truth_stale",
            )
            fault = manager.update(JointState.FAULT, 6.0, reason="camera_stream_fault")
            self.assertTrue(first.played)
            self.assertTrue(changed.played)
            self.assertTrue(fault.played)
            self.assertEqual(len(spoken), 3)
            self.assertTrue(all(sentence.count("。") == 1 for sentence in spoken))
            self.assertIn("双手", spoken[0])
            self.assertIn("机器状态", spoken[1])
            self.assertIn("摄像头断流", spoken[2])

    def test_voice_disabled_uses_one_system_fallback_sound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_assets(root)
            played: list[str] = []
            manager = AlertSoundManager(
                config(voice=False),
                root,
                player=lambda path, _volume: played.append(path.name) is None,
            )
            self.assertTrue(
                manager.update(JointState.UNCERTAIN, 2.0, reason="unknown").played
            )
            self.assertEqual(played, [SYSTEM_FALLBACK_FILE])

    def test_reason_mapping_never_repeats_raw_internal_text(self) -> None:
        sentence = system_sentence(JointState.UNCERTAIN, "unexpected_private_value")
        self.assertEqual(sentence, "当前证据不足，系统暂时无法判断。")

    def test_wide_or_multi_person_view_has_direct_one_sentence_reason(self) -> None:
        wide = system_sentence(JointState.UNCERTAIN, "single_wide_context_view_not_validated")
        multiple = system_sentence(JointState.UNCERTAIN, "multiple_people_in_single_station_view")
        self.assertEqual(wide.count("。"), 1)
        self.assertEqual(multiple.count("。"), 1)
        self.assertIn("近景机位", wide)
        self.assertIn("多名人员", multiple)

    def test_multistation_calibration_or_resolution_has_direct_reason(self) -> None:
        calibration = system_sentence(
            JointState.UNCERTAIN,
            "upper_station:station_crop_not_verified;station_zones_not_calibrated",
        )
        resolution = system_sentence(
            JointState.UNCERTAIN,
            "lower_station:source_resolution_below_manifest_minimum",
        )
        self.assertIn("标定", calibration)
        self.assertIn("分辨率", resolution)
        self.assertEqual(calibration.count("。"), 1)
        self.assertEqual(resolution.count("。"), 1)

    def test_generated_wav_assets_have_expected_duration(self) -> None:
        root = Path(__file__).resolve().parents[1] / "assets" / "sounds"
        expected = {
            "01-yellow-attention-2s-68bpm.wav": 2.0,
            "02-orange-warning-4p5s-112bpm.wav": 4.5,
            "03-red-danger-9s-176bpm.wav": 9.0,
            "04-purple-grey-system-unavailable.wav": 1.24,
        }
        for filename, expected_s in expected.items():
            with self.subTest(filename=filename), wave.open(str(root / filename), "rb") as source:
                self.assertEqual(source.getnchannels(), 1)
                self.assertEqual(source.getsampwidth(), 2)
                self.assertEqual(source.getframerate(), 48_000)
                duration = source.getnframes() / source.getframerate()
                self.assertAlmostEqual(duration, expected_s, places=2)

    def test_risk_files_share_tone_but_heartbeat_cadence_accelerates(self) -> None:
        root = Path(__file__).resolve().parents[1] / "assets" / "sounds"

        def frames(filename: str) -> bytes:
            with wave.open(str(root / filename), "rb") as source:
                return source.readframes(source.getnframes())

        def pulse_onsets(raw: bytes) -> int:
            values = array("h")
            values.frombytes(raw)
            if values.itemsize == 2 and sys.byteorder != "little":
                values.byteswap()
            window = 480  # 10 ms at 48 kHz
            active = [
                max(abs(value) for value in values[index : index + window]) > 1200
                for index in range(0, len(values), window)
            ]
            return sum(now and not before for before, now in zip([False, *active], active))

        attention = frames("01-yellow-attention-2s-68bpm.wav")
        warning = frames("02-orange-warning-4p5s-112bpm.wav")
        danger = frames("03-red-danger-9s-176bpm.wav")
        same_first_pulse = int(0.08 * 48_000) * 2
        self.assertEqual(warning[:same_first_pulse], attention[:same_first_pulse])
        self.assertEqual(danger[:same_first_pulse], attention[:same_first_pulse])
        rates = (
            pulse_onsets(attention) / 2.0,
            pulse_onsets(warning) / 4.5,
            pulse_onsets(danger) / 9.0,
        )
        self.assertLess(rates[0], rates[1])
        self.assertLess(rates[1], rates[2])


if __name__ == "__main__":
    unittest.main()
