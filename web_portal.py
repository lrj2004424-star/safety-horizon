"""Loopback-only review UI; shared review service remains the sole actuator gate.

本地审核界面：不上传视频；不绕过人工审核；不直接写蜂鸣器命令。
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import secrets
import threading
import time
import uuid
import webbrowser
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
ACTIONS = {"SELECT_EVENT", "SUBMIT_REVIEW", "ACKNOWLEDGE", "MANUAL_MISSED", "NEGATIVE_SAMPLE",
           "PROPOSE_THRESHOLDS", "APPLY_THRESHOLDS", "RUN_SIMULATION_TEST"}


def read_json(path):
    try:
        payload = json.loads(path.read_text())
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


class Portal:
    def __init__(self, src, *, port=8765):
        self.src = Path(src).resolve()
        self.runtime = self.src / "runtime"
        self.token = secrets.token_urlsafe(32)
        self.command_lock = threading.Lock()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Do not log capabilities or private event identifiers.
            def reply(self, status, data, mime="application/json"):
                if isinstance(data, dict):
                    data = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
            def authorized(self):
                # Host validation prevents DNS rebinding; capability protects local data.
                return (self.headers.get("Host") == owner.host and
                        secrets.compare_digest(self.headers.get("X-Safety-Token", ""), owner.token))
            def do_GET(self):
                parsed = urlparse(self.path)
                if self.headers.get("Host") != owner.host:
                    return self.reply(403, {"error": "invalid host"})
                static = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
                if parsed.path in static:
                    name, mime = static[parsed.path]
                    return self.reply(200, (ROOT / "ui" / name).read_bytes(), mime)
                if not self.authorized():
                    return self.reply(403, {"error": "unauthorized"})
                if parsed.path == "/api/state":
                    return self.reply(200, owner.state())
                if parsed.path == "/api/image":
                    path = owner.runtime / "touchdesigner-live-dashboard.jpg"
                    try:
                        if not 0 <= time.time() - path.stat().st_mtime < 2:
                            raise FileNotFoundError()
                        return self.reply(200, path.read_bytes(), "image/jpeg")
                    except OSError:
                        return self.reply(503, {"error": "No fresh frame / 无新鲜画面"})
                if parsed.path == "/api/frame":
                    try:
                        query = parse_qs(parsed.query)
                        jpg, count, fps = owner.frame(query.get("event", [""])[0], int(query.get("index", ["0"])[0]))
                        return self.reply(200, {"jpeg": jpg, "frames": count, "fps": fps})
                    except (OSError, ValueError, RuntimeError):
                        return self.reply(404, {"error": "Clip/frame unavailable / 片段或帧不可用"})
                return self.reply(404, {"error": "not found"})
            def do_POST(self):
                if not self.authorized() or self.headers.get("Origin") != owner.origin:
                    return self.reply(403, {"error": "unauthorized origin/capability"})
                if self.path != "/api/command":
                    return self.reply(404, {"error": "not found"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("invalid request length")
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict) or payload.get("action") not in ACTIONS:
                        raise ValueError("unsupported action")
                    code, result = owner.command(payload)
                    return self.reply(code, result)
                except (ValueError, OSError) as error:
                    return self.reply(400, {"error": str(error)})
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.origin = "http://" + self.host
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def state(self):
        path = self.runtime / "safety-officer-review-state.json"
        data = read_json(path)
        try:
            fresh = 0 <= time.time() - path.stat().st_mtime < 5
        except OSError:
            fresh = False
        if not fresh:
            data.update(service_state="STALE", actuator={"action": "SILENT"})
        data["fresh"] = fresh
        vision_path = self.src / "artifacts/ezviz-window-captures/live-status.json"
        data["vision"] = read_json(vision_path)
        try:
            vision_fresh = 0 <= time.time() - vision_path.stat().st_mtime < 2
        except OSError:
            vision_fresh = False
        if not vision_fresh:
            data["vision"] = {"state": "UNCERTAIN", "risk_score": None, "reason": "stale_or_missing"}
        return data

    def command(self, payload):
        from safety_monitor.review_store import ReviewStore
        if not self.command_lock.acquire(blocking=False):
            return 409, {"error": "Another command is pending / 上一操作未完成"}
        try:
            state = self.state()
            if not state["fresh"] or state.get("service_state") != "RUNNING":
                return 503, {"error": "Review service unavailable / 审核服务不可用"}
            path = self.runtime / "safety-officer-review-command.json"
            old = read_json(path)
            if old.get("command_id") and old["command_id"] != state.get("last_command_id"):
                return 409, {"error": "Previous command not acknowledged; do not repeat / 请等待或检查服务"}
            command = dict(payload, command_id=uuid.uuid4().hex)
            ReviewStore._write_json_atomic(path, command)
            for _ in range(50):
                result = self.state()
                if result.get("last_command_id") == command["command_id"]:
                    return 200, {"command_id": command["command_id"], **result.get("last_result", {})}
                time.sleep(0.1)
            return 202, {"ok": False, "command_id": command["command_id"], "message": "等待回执，未确认成功；请勿重复提交"}
        finally:
            self.command_lock.release()

    def frame(self, event, index):
        import base64
        import cv2
        row = next((r for r in self.state().get("queue", []) if r.get("event_id") == event), None)
        if not row or not row.get("clip_path"):
            raise ValueError("unknown event")
        path = Path(row["clip_path"])
        if not path.is_absolute():
            path = self.src / path
        path = path.resolve()
        if (self.src / "annotations").resolve() not in path.parents:
            raise ValueError("invalid clip path")
        cap = cv2.VideoCapture(str(path))
        try:
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if not 0 <= index < count:
                raise ValueError("invalid frame")
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, image = cap.read()
            if not ok:
                raise ValueError("cannot decode frame")
            ok, data = cv2.imencode(".jpg", image)
            if not ok:
                raise ValueError("cannot encode frame")
            return base64.b64encode(data).decode(), count, cap.get(cv2.CAP_PROP_FPS)
        finally:
            cap.release()

    def start(self, *, open_browser=True):
        self.thread.start()
        url = self.origin + "/#" + self.token
        print("Local review / 本地审核（仅本机，不分享此地址）: " + url, flush=True)
        if open_browser:
            webbrowser.open(url)

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()
