#!/usr/bin/env python3
"""Safety-officer review sidecar for the TouchDesigner workflow.

This process is intentionally file-based: TouchDesigner owns the visible UI,
while this verified Python runtime owns the durable queue, privacy-filtered
pre/post clips, annotations, statistics and the only actuator authorization.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from safety_monitor.pre_event_buffer import ReviewClipBuffer
from safety_monitor.review_contract import (
    ContractError,
    iso_utc,
    new_id,
    review_level,
    utc_now,
)
from safety_monitor.review_controller import ReviewController
from safety_monitor.review_store import ReviewStore
from safety_monitor.threshold_optimizer import ThresholdOptimizer


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_COMMAND = PROJECT_ROOT / "runtime" / "safety-officer-review-command.json"
DEFAULT_STATE = PROJECT_ROOT / "runtime" / "safety-officer-review-state.json"
DEFAULT_SOURCE = PROJECT_ROOT / "runtime" / "touchdesigner-opencv-result.jpg"
DEFAULT_PROPOSAL = PROJECT_ROOT / "config" / "fatigue-thresholds.proposed.json"
DEFAULT_ACTIVE_THRESHOLDS = PROJECT_ROOT / "config" / "fatigue-thresholds.active.json"
DEFAULT_INPUT_MODE = PROJECT_ROOT / "runtime" / "vision-input-mode.json"
DEFAULT_THRESHOLD_AUDIT = PROJECT_ROOT / "annotations" / "_state" / "threshold-approvals.jsonl"
DEFAULT_SIMULATION_LOG = PROJECT_ROOT / "annotations" / "_tests" / "simulation-tests.jsonl"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    ReviewStore._write_json_atomic(path, dict(payload))


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    """Append one audit entry via atomic file replacement."""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    line = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")) + "\n"
    ReviewStore._write_text_atomic(path, existing + line)


def _read_jsonl(path: Path, *, limit: int = 30) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    output: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            value = json.loads(line)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            output.append(value)
    return output


class SafetyOfficerService:
    def __init__(
        self,
        project_root: str | Path,
        *,
        command_path: str | Path | None = None,
        state_path: str | Path | None = None,
        frame_path: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.command_path = Path(command_path or self.project_root / "runtime" / DEFAULT_COMMAND.name).resolve()
        self.state_path = Path(state_path or self.project_root / "runtime" / DEFAULT_STATE.name).resolve()
        self.frame_path = Path(frame_path or self.project_root / "runtime" / DEFAULT_SOURCE.name).resolve()
        self.proposal_path = self.project_root / "config" / DEFAULT_PROPOSAL.name
        self.active_thresholds_path = self.project_root / "config" / DEFAULT_ACTIVE_THRESHOLDS.name
        self.input_mode_path = self.project_root / "runtime" / DEFAULT_INPUT_MODE.name
        self.threshold_audit_path = self.project_root / "annotations" / "_state" / DEFAULT_THRESHOLD_AUDIT.name
        self.simulation_log_path = self.project_root / "annotations" / "_tests" / DEFAULT_SIMULATION_LOG.name
        self.controller = ReviewController(self.project_root, station_ids=("upper_station",))
        self.clips = ReviewClipBuffer(
            self.project_root / "annotations",
            fps=8.0,
            pre_roll_s=3.0,
            post_roll_s=5.0,
            max_width=960,
        )
        self.last_source_mtime_ns = 0
        self.last_command_mtime_ns = 0
        self.last_command_id = ""
        self.last_result: dict[str, Any] = {
            "ok": True,
            "message": "审核服务已启动；未审核报警保持静音",
        }
        self.selected_event_id: str | None = None
        self.started_monotonic = time.monotonic()
        self._clip_started: set[str] = set()
        self._running = False

    def start(self) -> None:
        self.controller.start()
        # Never replace old evidence with footage captured after a restart.
        for item in self.controller.store.list_queue():
            event_id = str(item["event_id"])
            self._clip_started.add(event_id)
            if item.get("status") == "WAITING_CLIP":
                clip = dict(item.get("clip") or {})
                clip.update(status="FAILED", error="capture_interrupted_by_service_restart")
                self.controller.update_clip(event_id, clip)
        prior_state = _read_json(self.state_path) or {}
        self.last_command_id = str(prior_state.get("last_command_id", ""))
        try:
            self.last_command_mtime_ns = self.command_path.stat().st_mtime_ns
            old_command = _read_json(self.command_path) or {}
            old_id = str(old_command.get("command_id", ""))
            if old_id and old_id != self.last_command_id:
                self.last_result = {"ok": False, "message": "重启前未确认的操作已取消，请重新操作；不会重放旧命令"}
            self.last_command_id = old_id or self.last_command_id
        except FileNotFoundError:
            pass
        if not self.input_mode_path.exists():
            _write_json(
                self.input_mode_path,
                {
                    "schema_version": 1,
                    "mode": "LIVE",
                    "test_video": None,
                    "physical_actuator_allowed": True,
                    "updated_at_utc": iso_utc(),
                },
            )
        self._running = True
        self.publish_state()

    def stop(self) -> None:
        self._running = False
        self.controller.shutdown()
        self.clips.close()
        for event_id, clip in self.clips.drain_completed():
            self.controller.update_clip(event_id, clip)
        # A final state makes it visually obvious why the actuator is silent.
        self.last_result = {"ok": True, "message": "审核服务已停止；蜂鸣器已静音"}
        self.publish_state(service_state="STOPPED")

    def tick(self) -> None:
        now_s = time.monotonic()
        self._offer_latest_frame(now_s)
        poll = self.controller.poll_once()
        for event_id in poll.get("queued", []):
            item = self.controller.store.get_queue_item(str(event_id))
            if item is None or str(event_id) in self._clip_started:
                continue
            self._clip_started.add(str(event_id))
            system = item.get("system", {})
            clip = self.clips.start_event(
                str(event_id),
                station_id=str(item.get("station_id", "upper_station")),
                state=str(system.get("state", "UNCERTAIN")),
                trigger_s=now_s,
            )
            try:
                self.controller.update_clip(str(event_id), clip)
            except ContractError:
                pass
            if self.selected_event_id is None:
                self.selected_event_id = str(event_id)
        self.clips.tick(now_s)
        for event_id, clip in self.clips.drain_completed():
            try:
                self.controller.update_clip(event_id, clip)
            except ContractError as error:
                self.last_result = {"ok": False, "message": f"视频缓存更新失败：{error}"}
        self._process_command()
        self.publish_state()

    def _offer_latest_frame(self, now_s: float) -> None:
        try:
            stat = self.frame_path.stat()
            # A stale saved JPEG is not evidence captured after this restart.
            if not 0 <= time.time() - stat.st_mtime <= self.controller.max_status_age_s:
                return
            modified = stat.st_mtime_ns
            if modified == self.last_source_mtime_ns:
                return
            data = self.frame_path.read_bytes()
            self.last_source_mtime_ns = modified
            self.clips.offer_jpeg(data, now_s)
        except OSError:
            return

    def _pending_or_selected(self, requested: Any = None) -> str:
        event_id = str(requested or self.selected_event_id or "").strip()
        if event_id and self.controller.store.get_queue_item(event_id) is not None:
            return event_id
        pending = self.controller.pending_queue()
        if not pending:
            raise ContractError("当前没有可审核事件")
        self.selected_event_id = str(pending[0]["event_id"])
        return self.selected_event_id

    def _process_command(self) -> None:
        try:
            modified = self.command_path.stat().st_mtime_ns
            if modified == self.last_command_mtime_ns:
                return
            self.last_command_mtime_ns = modified
        except OSError:
            return
        command = _read_json(self.command_path)
        if command is None:
            self.last_result = {"ok": False, "message": "审核命令格式无效"}
            return
        command_id = str(command.get("command_id", "")).strip()
        if not command_id or command_id == self.last_command_id:
            return
        self.last_command_id = command_id
        action = str(command.get("action", "")).strip().upper()
        try:
            if action == "SELECT_EVENT":
                self.selected_event_id = self._pending_or_selected(command.get("event_id"))
                self.controller.begin_review(self.selected_event_id)
                self.last_result = {"ok": True, "message": "已进入逐帧审核"}
            elif action == "SUBMIT_REVIEW":
                event_id = self._pending_or_selected(command.get("event_id"))
                item = self.controller.store.get_queue_item(event_id)
                if item is None or item.get("status") not in {"PENDING_REVIEW", "IN_REVIEW"}:
                    raise ContractError("视频仍在缓存，请等待片段状态变为 READY")
                input_mode = _read_json(self.input_mode_path) or {}
                allow_actuation = (
                    input_mode.get("mode") == "LIVE"
                    and input_mode.get("physical_actuator_allowed") is True
                    and item.get("sample_origin") != "SIMULATION"
                )
                result = self.controller.submit_review(
                    event_id,
                    review_level=review_level(command.get("review_level")),
                    primary_action=str(command.get("primary_action", "OTHER")),
                    material_occluded=command.get("material_occluded", False),
                    hand_zone=str(command.get("hand_zone", "NOT_VISIBLE")),
                    frame_annotations=command.get("frame_annotations"),
                    notes=str(command.get("notes", "")),
                    allow_actuation=allow_actuation,
                )
                remaining = [
                    row
                    for row in self.controller.pending_queue()
                    if str(row.get("event_id")) != event_id
                ]
                self.selected_event_id = (
                    str(remaining[0]["event_id"]) if remaining else event_id
                )
                self.last_result = {
                    "ok": True,
                    "message": (
                        "审核已保存：测试样本或模式未授权，保持静音"
                        if not allow_actuation
                        else "审核已提交：两短一长提醒已授权一次"
                        if result["actuator"].get("action") == "ALERT"
                        else "审核已提交：1–2级判为误报候选，保持静音"
                    ),
                    "record_id": result["record"]["record_id"],
                }
            elif action == "ACKNOWLEDGE":
                event_id = self._pending_or_selected(command.get("event_id"))
                self.controller.acknowledge(event_id)
                self.last_result = {"ok": True, "message": "事件已归档并静音"}
            elif action in {"MANUAL_MISSED", "NEGATIVE_SAMPLE"}:
                raw = _read_json(self.controller.status_path)
                if raw is None:
                    raise ContractError("没有可用的当前视觉状态")
                if time.time() - self.controller.status_path.stat().st_mtime > self.controller.max_status_age_s:
                    raise ContractError("视觉状态已过期，不能作为当前补录证据")
                mode = _read_json(self.input_mode_path) or {}
                if mode.get("mode") not in {"LIVE", "TEST"}:
                    raise ContractError("输入模式无效，无法确定补录来源")
                item = self.controller.store.enqueue_manual_sample(
                    raw,
                    station_id="upper_station",
                    sample_origin="SIMULATION" if mode.get("mode") == "TEST" or raw.get("input_mode") == "TEST" else action,
                    clip={
                        "status": "UNAVAILABLE",
                        "path": None,
                        "pre_roll_s": 3,
                        "post_roll_s": 5,
                        "fps": 8,
                        "face_blurred": True,
                    },
                )
                event_id = str(item["event_id"])
                self._clip_started.add(event_id)
                clip = self.clips.start_event(
                    event_id, station_id=str(item["station_id"]),
                    state=str(item.get("system", {}).get("state", "UNCERTAIN")),
                    trigger_s=time.monotonic(),
                )
                self.controller.update_clip(event_id, clip)
                self.controller.publish_queue_view()
                self.selected_event_id = str(item["event_id"])
                self.last_result = {"ok": True, "message": "已加入人工抽检/漏报补录队列"}
            elif action == "PROPOSE_THRESHOLDS":
                optimizer = ThresholdOptimizer()
                proposal = optimizer.propose(self.project_root / "annotations")
                if proposal.get("status") == "PROPOSED":
                    optimizer.write_proposal(self.proposal_path, proposal)
                else:
                    _write_json(self.proposal_path, proposal)
                self.last_result = {
                    "ok": True,
                    "message": ("阈值建议已生成；仅为 PROPOSED，不会自动回写"
                                if proposal.get("status") == "PROPOSED"
                                else "样本不足或未满足优化条件；没有可应用的阈值建议"),
                    "proposal_status": proposal.get("status"),
                }
            elif action == "APPLY_THRESHOLDS":
                if command.get("explicit_approval") is not True:
                    raise ContractError("应用阈值必须由安全员明确点击审批")
                proposal = _read_json(self.proposal_path)
                if not isinstance(proposal, dict) or proposal.get("status") != "PROPOSED":
                    raise ContractError("当前没有达到样本门槛的 PROPOSED 阈值建议")
                if proposal.get("requires_explicit_approval") is not True or proposal.get("auto_apply") is not False:
                    raise ContractError("阈值建议缺少人工审批保护标记")
                suggested = proposal.get("suggested")
                if not isinstance(suggested, dict):
                    raise ContractError("阈值建议内容缺失")
                warning_min = int(suggested.get("warning_min"))
                danger_min = int(suggested.get("danger_min"))
                if not 1 <= warning_min < danger_min <= 100:
                    raise ContractError("阈值建议范围无效")
                approved_at = iso_utc()
                approved_by = str(command.get("approved_by", "touchdesigner_safety_officer"))[:120]
                active = {
                    "schema_version": 1,
                    "status": "ACTIVE",
                    "version": f"review-approved-{approved_at[:10]}-{new_id('v')[-8:]}",
                    "approved_at_utc": approved_at,
                    "approved_by": approved_by,
                    "explicit_approval": True,
                    "source_proposal_generated_at_utc": proposal.get("generated_at_utc"),
                    "thresholds": {
                        "warning_min": warning_min,
                        "danger_min": danger_min,
                    },
                    "effective_on_next_vision_engine_start": True,
                    "advisory_only": True,
                    "machine_control_enabled": False,
                }
                _write_json(self.active_thresholds_path, active)
                _append_jsonl(
                    self.threshold_audit_path,
                    {
                        "action": "APPROVE_SCORE_THRESHOLDS",
                        **active,
                    },
                )
                self.last_result = {
                    "ok": True,
                    "message": (
                        f"阈值已人工批准：趋势 {warning_min}，高风险 {danger_min}；"
                        "下次视觉引擎启动生效"
                    ),
                }
            elif action == "SET_INPUT_MODE":
                mode = str(command.get("mode", "LIVE")).upper()
                if mode not in {"LIVE", "TEST"}:
                    raise ContractError("输入模式必须是 LIVE 或 TEST")
                test_video = command.get("test_video")
                if mode == "TEST" and not test_video:
                    raise ContractError("请先在 simulation_video_input 选择项目 test_videos 内的视频")
                if mode == "TEST" and test_video:
                    test_path = Path(str(test_video)).expanduser().resolve()
                    allowed_root = (self.project_root / "test_videos").resolve()
                    if allowed_root not in test_path.parents:
                        raise ContractError("测试视频必须位于项目 test_videos 文件夹")
                    if not test_path.is_file() or test_path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".avi"}:
                        raise ContractError("测试视频不存在或格式不支持")
                _write_json(
                    self.input_mode_path,
                    {
                        "schema_version": 1,
                        "mode": mode,
                        "test_video": str(test_path) if mode == "TEST" else None,
                        # Test clips can never drive physical hardware.
                        "physical_actuator_allowed": mode == "LIVE",
                        "updated_at_utc": iso_utc(),
                    },
                )
                if mode == "TEST":
                    self.controller.store.publish_silent("test_mode_physical_output_disabled")
                self.last_result = {"ok": True, "message": f"输入模式已切换为 {mode}"}
            elif action == "RUN_SIMULATION_TEST":
                input_mode = _read_json(self.input_mode_path) or {}
                if str(input_mode.get("mode", "LIVE")).upper() != "TEST":
                    raise ContractError("运行模拟测试前必须先开启测试模式")
                expected_level = review_level(int(command.get("expected_level")))
                raw = _read_json(self.controller.status_path)
                if raw is None:
                    raise ContractError("没有可用的测试识别状态")
                state = str(raw.get("state", "UNCERTAIN")).upper()
                score = int(round(float(raw.get("risk_score", raw.get("score", 0)))))
                if expected_level <= 2:
                    matched = state in {"ACTIVE", "OBSERVING", "NORMAL", "ATTENTION", "CLEAR"}
                elif expected_level == 3:
                    matched = state in {"FATIGUE_TREND", "WARNING"}
                else:
                    matched = state in {"HIGH_RISK", "DANGER"}
                record = {
                    "schema_version": 1,
                    "timestamp_utc": iso_utc(),
                    "video": Path(str(input_mode.get("test_video"))).name,
                    "expected_level": expected_level,
                    "detected_state": state,
                    "detected_score": max(0, min(100, score)),
                    "match": bool(matched),
                    "physical_actuator_allowed": False,
                    "notes": str(command.get("notes", "TouchDesigner simulation test"))[:500],
                }
                _append_jsonl(self.simulation_log_path, record)
                self.controller.store.publish_silent("test_mode_physical_output_disabled")
                self.last_result = {
                    "ok": True,
                    "message": f"模拟测试已记录：{'匹配' if matched else '不匹配'}；声音保持关闭",
                }
            else:
                raise ContractError(f"不支持的审核动作：{action or '空'}")
        except (ContractError, ValueError, TypeError) as error:
            self.last_result = {"ok": False, "message": str(error)}

    def publish_state(self, *, service_state: str | None = None) -> None:
        rows = self.controller.queue_rows()
        pending = [row for row in rows if row.get("queue_status") in {"WAITING_CLIP", "PENDING_REVIEW", "IN_REVIEW"}]
        current_selected = next(
            (row for row in pending if row.get("event_id") == self.selected_event_id),
            None,
        )
        if pending and current_selected is None:
            self.selected_event_id = str(pending[0].get("event_id"))
        selected = next((row for row in rows if row.get("event_id") == self.selected_event_id), None)
        today = date.today().isoformat()
        stats = self.controller.statistics(day=today, station_id="upper_station")
        input_mode = _read_json(self.input_mode_path) or {"mode": "UNKNOWN", "physical_actuator_allowed": False}
        actuator = self.controller.store.read_actuator() or {}
        proposal = _read_json(self.proposal_path)
        payload = {
            "schema_version": 1,
            "updated_at_utc": iso_utc(),
            "service_state": service_state or ("RUNNING" if self._running else "STOPPED"),
            "uptime_s": round(max(0.0, time.monotonic() - self.started_monotonic), 1),
            "pending_count": len(pending),
            "reviewed_today": stats.get("reviewed_count", 0),
            "selected_event_id": self.selected_event_id,
            "selected": selected,
            "queue": rows,
            "statistics": stats,
            "actuator": actuator,
            "input_mode": input_mode,
            "threshold_proposal": proposal,
            "active_thresholds": _read_json(self.active_thresholds_path),
            "simulation_tests": _read_jsonl(self.simulation_log_path),
            "last_command_id": self.last_command_id,
            "last_result": self.last_result,
            "source_frame_age_ms": self._source_age_ms(),
            "advisory_only": True,
            "machine_control_enabled": False,
        }
        _write_json(self.state_path, payload)

    def _source_age_ms(self) -> int | None:
        try:
            return max(0, int(round((time.time() - self.frame_path.stat().st_mtime) * 1000)))
        except OSError:
            return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--command-json")
    parser.add_argument("--state-json")
    parser.add_argument("--frame-jpeg")
    parser.add_argument("--poll-s", type=float, default=0.10)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.poll_s < 0.05:
        raise ValueError("--poll-s must be at least 0.05")
    service = SafetyOfficerService(
        args.project_root,
        command_path=args.command_json,
        state_path=args.state_json,
        frame_path=args.frame_jpeg,
    )
    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    service.start()
    try:
        while running:
            service.tick()
            if args.once:
                break
            time.sleep(args.poll_s)
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
