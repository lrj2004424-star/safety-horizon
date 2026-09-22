#!/usr/bin/env python3
"""Simulate the reviewed Arduino UNO buzzer on this Mac.

This output-only fallback reads the same human-review actuator contract as
``indicator_bridge``. Raw OpenCV risk can never make it sound. Each authorized
review is played once as two short beeps followed by one long beep.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import struct
import subprocess
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from safety_monitor.review_contract import actuator_is_authorized, parse_utc

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-json",
        "--status-json",
        dest="review_json",
        required=True,
        help="Validated safety-officer actuator JSON; raw risk status is rejected.",
    )
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--audio", action="store_true", help="Play the computer simulation sound.")
    parser.add_argument(
        "--audio-if-no-uno",
        action="store_true",
        help="Play sound only while no Arduino UNO serial port is present (recommended).",
    )
    parser.add_argument("--poll-s", type=float, default=0.10)
    parser.add_argument("--max-age-s", type=float, default=1.5)
    return parser.parse_args()


def _read_state(
    path: Path,
    max_age_s: float,
) -> tuple[str, int, int, str, str | None, bool]:
    try:
        if time.time() - path.stat().st_mtime > max_age_s:
            # A closed launcher naturally stops updating the snapshot.  That
            # is a silent shutdown condition, not an audible worker alert.
            return "UNCERTAIN", 0, 0, "review_command_stale", None, False
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return "UNCERTAIN", 0, 0, "review_command_invalid", None, False
        if payload.get("action") == "SILENT":
            return (
                "UNCERTAIN",
                0,
                0,
                str(payload.get("reason", "review_not_authorized")),
                str(payload.get("command_id") or "") or None,
                False,
            )
        now = datetime.now(UTC)
        if not actuator_is_authorized(payload, now=now):
            return "UNCERTAIN", 0, 0, "review_command_not_authorized", None, False
        generated = parse_utc(payload.get("generated_at_utc"), field="generated_at_utc")
        if generated > now:
            return "UNCERTAIN", 0, 0, "review_command_from_future", None, False
        score = payload.get("risk_score", 0)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return "UNCERTAIN", 0, 0, "review_score_invalid", None, False
        return (
            "REVIEWED_ALERT",
            max(0, min(100, int(score))),
            3,
            "human_review_level_3_to_5",
            str(payload.get("command_id")),
            True,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "UNCERTAIN", 0, 0, "review_command_unavailable", None, False


def _pattern(level: int) -> tuple[str, float, float, int]:
    """Return Arduino-equivalent label, repeat period, on duration and frequency."""
    if level == 3:
        return "安全员确认 3–5 级：两短一长（仅一组）", 0.0, 1.10, 2300
    return "静音", 0.0, 0.0, 0


def _tone_file(runtime_dir: Path, frequency: int, duration_s: float) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / f"computer-buzzer-{frequency}-{int(duration_s * 1000)}ms.wav"
    if path.exists():
        return path
    sample_rate = 44_100
    count = max(1, int(sample_rate * duration_s))
    frames = bytearray()
    for index in range(count):
        envelope = min(1.0, index / max(1, sample_rate // 100), (count - index - 1) / max(1, sample_rate // 80))
        sample = int(0.28 * envelope * math.sin(2 * math.pi * frequency * index / sample_rate) * 32767)
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(frames))
    return path


def _review_pattern_file(runtime_dir: Path, frequency: int = 2300) -> Path:
    """Create the exact computer equivalent of the UNO two-short-one-long cue."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "computer-buzzer-two-short-one-long.wav"
    if path.exists():
        return path
    sample_rate = 44_100
    segments = (
        (0.12, True),
        (0.10, False),
        (0.12, True),
        (0.16, False),
        (0.60, True),
    )
    frames = bytearray()
    phase_index = 0
    for duration, sounding in segments:
        count = max(1, int(sample_rate * duration))
        for index in range(count):
            if sounding:
                edge = max(1, sample_rate // 120)
                envelope = min(1.0, index / edge, (count - index - 1) / edge)
                sample = int(
                    0.28
                    * max(0.0, envelope)
                    * math.sin(2 * math.pi * frequency * phase_index / sample_rate)
                    * 32767
                )
            else:
                sample = 0
            frames.extend(struct.pack("<h", sample))
            phase_index += 1
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(frames))
    return path


def _write_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _uno_present() -> bool:
    return any(Path("/dev").glob("cu.usbmodem*"))


class ReviewAudioPlayer:
    """Own the sound subprocess so silence/shutdown really stops playback."""

    def __init__(self):
        self.process = None

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=0.5)
        self.process = None

    def play(self, path):
        self.stop()
        self.process = subprocess.Popen(
            ["/usr/bin/afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def _run(args, player, running) -> int:
    status_path = Path(args.review_json).expanduser().resolve()
    output_path = Path(args.output_json).expanduser().resolve()
    runtime_dir = output_path.parent
    last_beep = 0.0
    last_command_id: str | None = None
    last_signature: tuple[str, int, int, str, bool, str | None] | None = None
    while running():
        state, score, level, reason, command_id, authorized = _read_state(
            status_path,
            args.max_age_s,
        )
        label, period_s, duration_s, frequency = _pattern(level)
        now = time.monotonic()
        audio_enabled = bool(args.audio or (args.audio_if_no_uno and not _uno_present()))
        signature = (state, score, level, reason, audio_enabled, command_id)
        should_beep = bool(
            audio_enabled
            and authorized
            and command_id
            and command_id != last_command_id
        )
        if should_beep:
            sound = _review_pattern_file(runtime_dir, frequency)
            player.play(sound)
            last_beep = now
        elif not authorized or not audio_enabled:
            player.stop()
        # Observe a hardware-owned decision too: unplugging UNO must not
        # replay that same decision through the computer loudspeaker.
        if authorized and command_id:
            last_command_id = command_id
        if signature != last_signature or should_beep:
            _write_output(output_path, {
                "mode": "computer_buzzer_simulator",
                "audio_enabled": audio_enabled,
                "state": state,
                "risk_score": score,
                "buzzer_level": level,
                "pattern": label,
                "last_beep_monotonic_s": round(last_beep, 2) if last_beep else None,
                "reason": reason,
                "command_id": command_id,
                "review_authorized": authorized,
            })
            last_signature = signature
        time.sleep(max(0.03, args.poll_s))
    return 0


def main() -> int:
    args = parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (args.poll_s, args.max_age_s)):
        raise ValueError("poll and max age must be finite and positive")
    player = ReviewAudioPlayer()
    running = True
    def stop(_signal, _frame):
        nonlocal running
        running = False
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return _run(args, player, lambda: running)
    finally:
        player.stop()
        _write_output(Path(args.output_json).expanduser().resolve(), {
            "mode": "computer_buzzer_simulator", "state": "STOPPED",
            "audio_enabled": False, "buzzer_level": 0, "pattern": "SILENT",
            "review_authorized": False, "reason": "simulator_stopped",
        })
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
