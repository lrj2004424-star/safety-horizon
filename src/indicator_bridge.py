#!/usr/bin/env python3
"""Forward *review-authorized* advisory commands to an output-only Arduino.

The raw OpenCV status is intentionally not an actuator input. A safety
officer must submit a 3--5 review before ``review-actuator.json`` can produce
one audible ``two short, one long`` pattern. Missing, stale, malformed or
unreviewed data always becomes a silent command.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from safety_monitor.review_contract import actuator_is_authorized, parse_utc

ALLOWED_STATES = frozenset({"NORMAL", "ATTENTION", "WARNING", "DANGER", "UNCERTAIN", "FAULT"})
# Kept local so the tiny serial bridge does not need OpenCV/MediaPipe imports.
# Values mirror safety_monitor.fatigue_status and form the stable wire contract.
FATIGUE_OUTPUTS = {
    "ACTIVE": (0, 0),
    "OBSERVING": (1, 0),
    "UNCERTAIN": (2, 0),
    "FATIGUE_TREND": (3, 2),
    "HIGH_RISK": (4, 3),
}
FATIGUE_STATES = frozenset(FATIGUE_OUTPUTS)
REVIEW_PATTERN_DURATION_S = 1.20


def encode_silent_uncertain() -> bytes:
    """Silence an attached UNO when the launcher is closed or stale."""
    return b"FATIGUE 2 0 0\n"


def state_from_snapshot(path: str | Path, max_age_s: float) -> tuple[str, str]:
    snapshot = Path(path)
    try:
        age_s = max(0.0, time.time() - snapshot.stat().st_mtime)
        if age_s > max_age_s:
            return "FAULT", "status_snapshot_stale"
        payload: Any = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return "FAULT", "status_snapshot_invalid"
        if payload.get("machine_control_enabled") is not False:
            return "FAULT", "unsafe_status_contract"
        state = str(payload.get("state", "")).upper()
        if state not in ALLOWED_STATES:
            return "FAULT", "status_state_invalid"
        return state, str(payload.get("reason", ""))
    except FileNotFoundError:
        return "FAULT", "status_snapshot_missing"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "FAULT", "status_snapshot_invalid"


def encode_command(state: str) -> bytes:
    normalized = state.upper()
    if normalized not in ALLOWED_STATES:
        normalized = "FAULT"
    return f"STATE {normalized}\n".encode("ascii")


def encode_fatigue_command(state: str, score: int) -> bytes:
    """Encode the stable Arduino fatigue protocol.

    Wire format: FATIGUE <state_code> <risk_score> <buzzer_level>\n
    """
    output = FATIGUE_OUTPUTS.get(state.upper())
    if output is None:
        return encode_command("FAULT")
    state_code, buzzer_level = output
    bounded_score = max(0, min(100, int(score)))
    return (
        f"FATIGUE {state_code} {bounded_score} {buzzer_level}\n"
    ).encode("ascii")


def encode_review_alert(score: int) -> bytes:
    """Start one reviewed two-short-one-long pattern on the UNO."""
    bounded_score = max(0, min(100, int(score)))
    return f"FATIGUE 4 {bounded_score} 3\n".encode("ascii")


def encode_review_hold(score: int) -> bytes:
    """Keep the reviewed-risk light visible after the one-shot sound."""
    bounded_score = max(0, min(100, int(score)))
    return f"FATIGUE 4 {bounded_score} 0\n".encode("ascii")


def reviewed_command_from_snapshot(
    path: str | Path,
    max_age_s: float,
    *,
    now: datetime | None = None,
) -> tuple[bytes, str, str | None, bool, int]:
    """Validate the human-review actuator contract.

    Returns ``(wire_command, reason, command_id, authorized, score)``. This is
    the only reader used by :func:`main`; the legacy raw-status helper below is
    retained for old diagnostics and is not an actuator path.
    """
    snapshot = Path(path)
    moment = now or datetime.now(UTC)
    try:
        age_s = max(0.0, time.time() - snapshot.stat().st_mtime)
        if age_s > max_age_s:
            return encode_silent_uncertain(), "review_command_stale", None, False, 0
        payload: Any = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return encode_silent_uncertain(), "review_command_invalid", None, False, 0
        if payload.get("action") == "SILENT":
            return (
                encode_silent_uncertain(),
                str(payload.get("reason", "review_not_authorized")),
                str(payload.get("command_id") or "") or None,
                False,
                0,
            )
        if not actuator_is_authorized(payload, now=moment):
            return encode_silent_uncertain(), "review_command_not_authorized", None, False, 0
        generated = parse_utc(payload.get("generated_at_utc"), field="generated_at_utc")
        if generated > moment:
            return encode_silent_uncertain(), "review_command_from_future", None, False, 0
        score = payload.get("risk_score", 0)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return encode_silent_uncertain(), "review_score_invalid", None, False, 0
        bounded_score = max(0, min(100, int(score)))
        command_id = str(payload.get("command_id"))
        return (
            encode_review_alert(bounded_score),
            "human_review_level_3_to_5",
            command_id,
            True,
            bounded_score,
        )
    except FileNotFoundError:
        return encode_silent_uncertain(), "review_command_missing", None, False, 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return encode_silent_uncertain(), "review_command_invalid", None, False, 0


def command_from_snapshot(path: str | Path, max_age_s: float) -> tuple[bytes, str]:
    """Read either the legacy safety state or the whole-person fatigue state."""
    snapshot = Path(path)
    try:
        age_s = max(0.0, time.time() - snapshot.stat().st_mtime)
        if age_s > max_age_s:
            return encode_silent_uncertain(), "status_snapshot_stale"
        payload: Any = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return encode_command("FAULT"), "status_snapshot_invalid"
        if payload.get("machine_control_enabled") is not False:
            return encode_command("FAULT"), "unsafe_status_contract"
        state = str(payload.get("state", "")).upper()
        if state in FATIGUE_STATES:
            score = payload.get("risk_score", payload.get("score", 0))
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                return encode_command("FAULT"), "status_score_invalid"
            return encode_fatigue_command(state, int(score)), str(payload.get("reason", ""))
        if state in ALLOWED_STATES:
            return encode_command(state), str(payload.get("reason", ""))
        return encode_command("FAULT"), "status_state_invalid"
    except FileNotFoundError:
        return encode_silent_uncertain(), "status_snapshot_missing"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return encode_command("FAULT"), "status_snapshot_invalid"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-json",
        "--status-json",
        dest="review_json",
        default="runtime/review-actuator.json",
        help="Validated safety-officer actuator JSON; raw OpenCV status is never accepted here.",
    )
    parser.add_argument("--serial-port", help="Example: /dev/cu.usbmodem1101")
    parser.add_argument("--auto-serial", action="store_true", help="Reconnect one uniquely identified genuine UNO; ambiguous/unknown devices are not opened")
    parser.add_argument("--connection-json", help="Publish actual connection state for the TD status card")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--max-age-s", type=float, default=1.5)
    parser.add_argument("--heartbeat-s", type=float, default=0.5)
    parser.add_argument("--dry-run", action="store_true", help="Print commands without opening serial")
    parser.add_argument("--once", action="store_true", help="Send/print one command and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (args.max_age_s, args.heartbeat_s)):
        raise ValueError("--max-age-s and --heartbeat-s must be positive")
    serial_device = None
    connection = None
    if not args.dry_run:
        if not args.serial_port and not getattr(args, "auto_serial", False):
            raise ValueError("--serial-port is required unless --dry-run is used")
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "pyserial is required for hardware output; install requirements-hardware.txt"
            ) from exc
        if getattr(args, "auto_serial", False):
            from serial.tools import list_ports
            from safety_monitor.serial_connection import SerialConnection
            connection = SerialConnection(serial.Serial, list_ports.comports,
                port=args.serial_port, baudrate=args.baudrate)
        else:
            serial_device = serial.Serial(args.serial_port, args.baudrate, timeout=0, write_timeout=0.25)
    last_command: bytes | None = None
    last_sent = 0.0
    last_consumed_id: str | None = None
    alert_until = 0.0
    reviewed_score = 0
    connection_generation = 0
    last_connection_report = -1.0
    def publish_connection(now, stopped=False):
        nonlocal last_connection_report
        path = getattr(args, "connection_json", None)
        if not path or (not stopped and now - last_connection_report < 0.5):
            return
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "connected": bool(not stopped and connection and connection.ready),
            "reason": "stopped" if stopped else connection.reason if connection else "manual_port",
            "port": connection.port if connection else args.serial_port,
            "updated_at": time.time(), "machine_control_enabled": False,
        }), encoding="utf-8")
        temporary.replace(destination)
        last_connection_report = now
    running = True
    def stop(_signal, _frame):
        nonlocal running
        running = False
    previous_handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        while running:
            requested, reason, command_id, authorized, score = reviewed_command_from_snapshot(
                args.review_json,
                args.max_age_s,
            )
            now = time.monotonic()
            if connection is not None:
                ready = connection.tick(now)
                publish_connection(now)
                if not ready or connection_generation != connection.generation:
                    # Missing-board and reconnect observations are consumed,
                    # never queued for later physical replay.
                    last_consumed_id = command_id or last_consumed_id
                    connection_generation = connection.generation
                    alert_until = 0.0
                    last_command = None
                    if args.once:
                        return 0
                    time.sleep(0.05)
                    continue
            if authorized and command_id != last_consumed_id:
                command = requested
                last_consumed_id = command_id
                reviewed_score = score
                alert_until = now + REVIEW_PATTERN_DURATION_S
            elif authorized and command_id == last_consumed_id and now < alert_until:
                # A heartbeat during the pattern could restart it on the UNO.
                time.sleep(min(0.03, args.heartbeat_s / 3.0))
                continue
            elif last_consumed_id and authorized and command_id == last_consumed_id:
                command = encode_review_hold(reviewed_score)
                reason = "reviewed_alert_completed_sound_is_silent"
            else:
                command = encode_silent_uncertain()
            if command != last_command or now - last_sent >= args.heartbeat_s:
                if args.dry_run:
                    print(json.dumps({"command": command.decode().strip(), "reason": reason}, ensure_ascii=False))
                elif connection is not None:
                    connection.send(command)
                else:
                    assert serial_device is not None
                    serial_device.write(command)
                    serial_device.flush()
                last_command, last_sent = command, now
            if args.once:
                return 0
            time.sleep(min(0.05, args.heartbeat_s / 2.0))
    finally:
        if connection is not None:
            connection.send(encode_silent_uncertain())
            connection.close()
            publish_connection(time.monotonic(), stopped=True)
        if serial_device is not None:
            try:
                serial_device.write(encode_silent_uncertain())
                serial_device.flush()
            finally:
                serial_device.close()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
