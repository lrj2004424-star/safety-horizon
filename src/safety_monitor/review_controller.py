"""Application-facing API for the safety-officer review gate.

TouchDesigner may keep one instance of :class:`ReviewController` in module
memory.  A standalone sidecar may call :meth:`poll_once` in a loop.  In both
cases raw vision status can only enter the review queue; actuator output is
created solely by :meth:`submit_review`.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .review_contract import ContractError, iso_utc, safe_identifier, utc_now
from .review_store import ReviewStore


DEFAULT_STATUS_RELATIVE = Path("artifacts/ezviz-window-captures/live-status.json")
DEFAULT_ANNOTATIONS_RELATIVE = Path("annotations")
DEFAULT_ACTUATOR_RELATIVE = Path("runtime/review-actuator.json")
DEFAULT_QUEUE_VIEW_RELATIVE = Path("runtime/review-queue.json")


class ReviewController:
    def __init__(
        self,
        project_root: str | Path,
        *,
        status_path: str | Path | None = None,
        annotations_root: str | Path | None = None,
        actuator_path: str | Path | None = None,
        queue_view_path: str | Path | None = None,
        station_ids: Sequence[str] = ("upper_station",),
        max_status_age_s: float = 1.5,
        now=utc_now,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.status_path = self._resolve(status_path, DEFAULT_STATUS_RELATIVE)
        self.annotations_root = self._resolve(annotations_root, DEFAULT_ANNOTATIONS_RELATIVE)
        self.actuator_path = self._resolve(actuator_path, DEFAULT_ACTUATOR_RELATIVE)
        self.queue_view_path = self._resolve(queue_view_path, DEFAULT_QUEUE_VIEW_RELATIVE)
        self.station_ids = tuple(safe_identifier(value, field="station_id") for value in station_ids)
        if not self.station_ids:
            raise ContractError("at least one station_id is required")
        if max_status_age_s <= 0:
            raise ContractError("max_status_age_s must be positive")
        self.max_status_age_s = float(max_status_age_s)
        self._now = now
        self.store = ReviewStore(
            self.annotations_root,
            actuator_path=self.actuator_path,
            now=now,
        )
        self.last_error: str | None = None

    def start(self) -> dict[str, Any]:
        """Start from an explicitly silent output state."""
        command = self.store.publish_silent("review_controller_started", generated_at=self._now())
        self.publish_queue_view()
        return command

    def shutdown(self) -> dict[str, Any]:
        """Guarantee a visible, fresh silent command during orderly shutdown."""
        command = self.store.publish_silent("review_controller_stopped", generated_at=self._now())
        self.publish_queue_view()
        return command

    def ingest_payload(
        self,
        payload: Mapping[str, Any],
        *,
        clip_by_station: Mapping[str, Mapping[str, Any]] | None = None,
        created_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Put eligible raw risks in the queue without touching the actuator."""
        if not isinstance(payload, Mapping):
            raise ContractError("live status must be an object")
        created: list[dict[str, Any]] = []
        for station_id in self.station_ids:
            clip = clip_by_station.get(station_id) if clip_by_station else None
            item = self.store.ingest_snapshot(
                payload,
                station_id=station_id,
                clip=clip,
                created_at=created_at or self._now(),
            )
            if item is not None:
                created.append(item)
        self.publish_queue_view()
        return created

    def ingest_status_file(self) -> list[dict[str, Any]]:
        """Read one fresh atomic live-status snapshot and ingest it."""
        try:
            age_s = max(0.0, time.time() - self.status_path.stat().st_mtime)
            if age_s > self.max_status_age_s:
                self.last_error = "status_snapshot_stale"
                return []
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ContractError("live status must be an object")
            if payload.get("machine_control_enabled") is not False:
                raise ContractError("unsafe live status contract")
            self.last_error = None
            return self.ingest_payload(payload)
        except FileNotFoundError:
            self.last_error = "status_snapshot_missing"
            return []
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ContractError) as exc:
            self.last_error = f"status_snapshot_invalid:{type(exc).__name__}"
            return []

    def poll_once(self) -> dict[str, Any]:
        """One sidecar tick: ingest, expire one-shot output, publish UI rows."""
        queued = self.ingest_status_file()
        actuator = self.store.expire_actuator(now=self._now())
        self.publish_queue_view()
        return {
            "timestamp_utc": iso_utc(self._now()),
            "queued": [item["event_id"] for item in queued],
            "pending_count": len(self.pending_queue()),
            "actuator": actuator,
            "last_error": self.last_error,
        }

    def pending_queue(self) -> list[dict[str, Any]]:
        return self.store.list_queue(statuses={"WAITING_CLIP", "PENDING_REVIEW", "IN_REVIEW"})

    def queue_rows(self) -> list[dict[str, Any]]:
        """Return flat values that can populate a TouchDesigner Table DAT."""
        rows: list[dict[str, Any]] = []
        for item in self.store.list_queue():
            system = item.get("system", {})
            clip = item.get("clip", {})
            review = item.get("review") or {}
            rows.append(
                {
                    "event_id": item.get("event_id"),
                    "time": item.get("created_at_utc"),
                    "station": item.get("station_id"),
                    "system_state": system.get("state"),
                    "alert_class": system.get("alert_class"),
                    "risk_score": system.get("latest_risk_score", system.get("risk_score")),
                    "peak_score": system.get("peak_risk_score", system.get("risk_score")),
                    "queue_status": item.get("status"),
                    "clip_status": clip.get("status"),
                    "clip_path": clip.get("path"),
                    "review_level": review.get("review_level"),
                }
            )
        return rows

    def publish_queue_view(self) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "updated_at_utc": iso_utc(self._now()),
            "rows": self.queue_rows(),
        }
        ReviewStore._write_json_atomic(self.queue_view_path, payload)
        return payload

    def update_clip(self, event_id: str, clip: Mapping[str, Any]) -> dict[str, Any]:
        item = self.store.update_clip(event_id, clip, updated_at=self._now())
        self.publish_queue_view()
        return item

    def begin_review(self, event_id: str) -> dict[str, Any]:
        item = self.store.begin_review(event_id, updated_at=self._now())
        self.publish_queue_view()
        return item

    def submit_review(
        self,
        event_id: str,
        *,
        review_level: int,
        primary_action: str,
        material_occluded: bool,
        hand_zone: str = "NOT_VISIBLE",
        frame_annotations: Sequence[Mapping[str, Any]] | None = None,
        notes: str = "",
        active_seconds: float = 2.2,
        allow_actuation: bool = True,
    ) -> dict[str, Any]:
        record, actuator = self.store.submit_review(
            event_id,
            review_level=review_level,
            primary_action=primary_action,
            material_occluded=material_occluded,
            hand_zone=hand_zone,
            frame_annotations=frame_annotations,
            notes=notes,
            submitted_at=self._now(),
            active_seconds=active_seconds,
            allow_actuation=allow_actuation,
        )
        self.publish_queue_view()
        return {"record": record, "actuator": actuator}

    def acknowledge(self, event_id: str) -> dict[str, Any]:
        command = self.store.acknowledge(event_id, acknowledged_at=self._now())
        self.publish_queue_view()
        return command

    def statistics(self, *, day=None, station_id: str | None = None) -> dict[str, Any]:
        return self.store.statistics(day=day, station_id=station_id)

    def _resolve(self, supplied: str | Path | None, default: Path) -> Path:
        if supplied is None:
            return (self.project_root / default).resolve()
        value = Path(supplied).expanduser()
        return value.resolve() if value.is_absolute() else (self.project_root / value).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safety-officer queue sidecar")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--station-id", action="append", dest="station_ids")
    parser.add_argument("--poll-s", type=float, default=0.20)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.poll_s < 0.05:
        raise ContractError("poll-s must be at least 0.05")
    controller = ReviewController(
        args.project_root,
        station_ids=tuple(args.station_ids or ["upper_station"]),
    )
    controller.start()
    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while running:
            result = controller.poll_once()
            if args.once:
                print(json.dumps(result, ensure_ascii=False))
                break
            time.sleep(args.poll_s)
    finally:
        controller.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
