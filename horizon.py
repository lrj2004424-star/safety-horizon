#!/usr/bin/env python3
"""Safety Horizon entry point / 独立于 TouchDesigner 的统一入口。"""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import platform
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))


def doctor():
    checks = {"python_3_12": sys.version_info[:2] == (3, 12)}
    for module in ("cv2", "mediapipe", "numpy", "serial"):
        checks[module] = importlib.util.find_spec(module) is not None
    checks["models"] = all((SRC / "models" / name).is_file() for name in
                            ("pose_landmarker_full.task", "pose_landmarker_lite.task", "hand_landmarker.task"))
    print(json.dumps({"checks": checks, "python": platform.python_version(),
        "platform": platform.platform(), "live_capture_helper": (SRC / "macos_window_capture/ezviz_window_capture_cg").is_file(),
        "production_approved": False}, indent=2))
    return 0 if all(checks.values()) else 2


def run(args):
    from safety_monitor.review_store import ReviewStore
    from web_portal import Portal
    import fcntl
    if args.alerts != "none" and (args.test_video or args.no_vision):
        raise ValueError("Test/no-camera sessions cannot start audible outputs")
    if not args.no_vision and doctor():
        return 2
    if not args.no_vision and not args.test_video and platform.system() != "Darwin":
        raise ValueError("EZVIZ window capture requires macOS; other platforms are not release-validated")
    runtime = SRC / "runtime"
    runtime.mkdir(exist_ok=True)
    lock_file = (runtime / "standalone.lock").open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError("Another Safety Horizon session is active / 已有实例运行")
    children, log_handles = [], []
    portal = None
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    def spawn(name, argv):
        handle = (runtime / (name + ".log")).open("ab")
        log_handles.append(handle)
        p = subprocess.Popen([sys.executable, *argv], cwd=SRC, stdin=subprocess.DEVNULL,
                             stdout=handle, stderr=handle)
        children.append((name, p))
        return p
    try:
        test_file = None
        if args.test_video:
            test_file = Path(args.test_video).expanduser().resolve()
            if (SRC / "test_videos").resolve() not in test_file.parents or not test_file.is_file():
                raise ValueError("Put authorized test video inside src/test_videos first")
        mode = "TEST" if test_file or args.no_vision else "LIVE"
        ReviewStore._write_json_atomic(runtime / "vision-input-mode.json", {
            "schema_version": 1, "mode": mode, "test_video": str(test_file) if test_file else None,
            "physical_actuator_allowed": mode == "LIVE" and args.alerts != "none"})
        # Clear previous authorization before starting any output consumer.
        ReviewStore(SRC / "annotations", actuator_path=runtime / "review-actuator.json").publish_silent("new_session_requires_new_review")
        spawn("review-supervisor", ["supervise_service.py", "--state-json", str(runtime / "review-supervisor.json"),
              "--", sys.executable, "safety_officer_review.py", "--project-root", str(SRC)])
        if not args.no_vision:
            argv = ["ezviz_window_monitor.py", "--headless", "--fps", "20", "--window-title", "萤石云视频",
                    "--manifest", str(SRC / "config/ezviz-fatigue-single-upper.json"), "--dashboard-output-fps", "12"]
            for flag, name in {"dashboard-output": "live-dashboard", "source-preview-output": "source-overview",
                               "station-input-output": "left-station-input", "vision-output": "opencv-result",
                               "risk-card-output": "risk-decision"}.items():
                argv += ["--" + flag, str(runtime / ("touchdesigner-" + name + ".jpg"))]
            argv += ["--input-mode-json", str(runtime / "vision-input-mode.json"),
                     "--active-thresholds-json", str(SRC / "config/fatigue-thresholds.active.json")]
            spawn("vision", argv)
        if args.alerts == "uno":
            spawn("arduino", ["indicator_bridge.py", "--review-json", str(runtime / "review-actuator.json"),
                "--auto-serial", "--connection-json", str(runtime / "arduino-connection.json")])
        if args.alerts == "computer":
            spawn("buzzer", ["computer_buzzer_simulator.py", "--review-json", str(runtime / "review-actuator.json"),
                "--output-json", str(runtime / "computer-buzzer-simulator.json"), "--audio-if-no-uno"])
        if not args.no_web:
            portal = Portal(SRC, port=args.port)
            portal.start(open_browser=not args.no_browser)
        print("Safety Horizon running / 运行中；Ctrl+C 退出。Alerts / 声音: " + args.alerts, flush=True)
        while running:
            if args.parent_pid and os.getppid() != args.parent_pid:
                break  # TD force-quit must not leave its private session alive.
            dead = [(n, p.returncode) for n, p in children if p.poll() is not None]
            if dead:
                raise RuntimeError(f"Child service stopped: {dead}; stopping owned session safely")
            time.sleep(0.25)
    finally:
        if portal:
            portal.close()
        ReviewStore(SRC / "annotations", actuator_path=runtime / "review-actuator.json").publish_silent("session_stopping")
        # Review exits first and writes SILENT while consumers are still alive.
        for _, p in children:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
        for f in log_handles:
            f.close()
        lock_file.close()
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Environment checks / 环境检查")
    sub.add_parser("test", help="Regression tests / 回归测试，不操作硬件")
    r = sub.add_parser("run", help="Start all core services / 启动完整独立工作流")
    r.add_argument("--no-vision", action="store_true", help="UI/review service only; no synthetic detections")
    r.add_argument("--no-browser", action="store_true")
    r.add_argument("--no-web", action="store_true", help="For TouchDesigner only")
    r.add_argument("--test-video", help="Authorized file under src/test_videos")
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--alerts", choices=["none", "computer", "uno"], default="none")
    r.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.command == "doctor":
        return doctor()
    if args.command == "test":
        for directory, cwd in (("tests", SRC), ("release_tests", ROOT)):
            code = subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", directory, "-v"], cwd=cwd)
            if code:
                return code
        return 0
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
