#!/usr/bin/env python3
"""Generate one heartbeat-like risk family at three tempos and durations."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 48_000
RISK_SPECS = {
    "01-yellow-attention-2s-68bpm.wav": (2.0, 68.0),
    "02-orange-warning-4p5s-112bpm.wav": (4.5, 112.0),
    "03-red-danger-9s-176bpm.wav": (9.0, 176.0),
}


def _tone(frequency: float, seconds: float, amplitude: float) -> list[float]:
    count = max(1, int(round(seconds * SAMPLE_RATE)))
    if frequency <= 0 or amplitude <= 0:
        return [0.0] * count
    attack = max(1, int(0.012 * SAMPLE_RATE))
    release = max(1, int(0.022 * SAMPLE_RATE))
    output: list[float] = []
    for index in range(count):
        phase = 2.0 * math.pi * frequency * index / SAMPLE_RATE
        envelope = min(1.0, index / attack, (count - index - 1) / release)
        value = math.sin(phase) + 0.16 * math.sin(2.0 * phase)
        output.append(amplitude * max(0.0, envelope) * value / 1.16)
    return output


def _risk_heartbeat(total_seconds: float, beats_per_minute: float) -> list[float]:
    """Use one 640 Hz double-beat shape; only beat cadence changes."""
    first_pulse_s = 0.09
    between_pulses_s = 0.07
    second_pulse_s = 0.12
    beat_shape_s = first_pulse_s + between_pulses_s + second_pulse_s
    beat_period_s = 60.0 / beats_per_minute
    rest_s = max(0.0, beat_period_s - beat_shape_s)
    samples: list[float] = []
    target = int(round(total_seconds * SAMPLE_RATE))

    def append_segment(frequency: float, seconds: float, amplitude: float) -> None:
        remaining_s = (target - len(samples)) / SAMPLE_RATE
        if remaining_s > 0:
            samples.extend(_tone(frequency, min(seconds, remaining_s), amplitude))

    while len(samples) < target:
        append_segment(640.0, first_pulse_s, 0.48)
        append_segment(0.0, between_pulses_s, 0.0)
        append_segment(640.0, second_pulse_s, 0.56)
        append_segment(0.0, rest_s, 0.0)
    return samples[:target]


def _system_fallback() -> list[float]:
    """One neutral technical cue used only when text-to-speech is unavailable."""
    return (
        _tone(330.0, 0.30, 0.40)
        + _tone(0.0, 0.16, 0.0)
        + _tone(330.0, 0.30, 0.40)
        + _tone(0.0, 0.14, 0.0)
        + _tone(260.0, 0.34, 0.34)
    )


def _write_wav(path: Path, samples: list[float]) -> None:
    pcm = b"".join(
        struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))
        for value in samples
    )
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(pcm)


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, (duration, beats_per_minute) in RISK_SPECS.items():
        path = output_dir / filename
        _write_wav(path, _risk_heartbeat(duration, beats_per_minute))
        print(
            f"{path}: {duration:.1f}s / {beats_per_minute:.0f} BPM "
            "- same 640 Hz double-heartbeat"
        )
    fallback = output_dir / "04-purple-grey-system-unavailable.wav"
    samples = _system_fallback()
    _write_wav(fallback, samples)
    print(f"{fallback}: {len(samples) / SAMPLE_RATE:.2f}s - voice-unavailable fallback")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="assets/sounds")
    args = parser.parse_args()
    generate(Path(args.output_dir).expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
