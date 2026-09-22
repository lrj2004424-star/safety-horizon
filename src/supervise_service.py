#!/usr/bin/env python3
"""Own one worker, restart exited workers with bounded backoff, expose health."""
from __future__ import annotations
import argparse
from collections import deque
import json
import os
from pathlib import Path
import signal
import subprocess
import time


class WorkerSupervisor:
    def __init__(self, command, *, spawn=subprocess.Popen, limit=5, window_s=300):
        if not command or limit < 1 or window_s <= 0:
            raise ValueError("command and positive retry limits are required")
        self.command, self.spawn = command, spawn
        self.limit, self.window_s = limit, window_s
        self.attempts = deque()
        self.worker = None
        self.next_start = 0.0
        self.starts = 0
        self.last_exit = None
        self.state = "STARTING"

    def tick(self, now):
        if self.state in {"STOPPED", "FAULT_RETRY_LIMIT"}:
            return
        if self.worker is not None:
            code = self.worker.poll()
            if code is None:
                return
            self.last_exit, self.worker = code, None
            self.next_start = now + min(30, 2 ** min(5, len(self.attempts)))
            self.state = "BACKOFF"
        if now < self.next_start:
            return
        while self.attempts and now - self.attempts[0] >= self.window_s:
            self.attempts.popleft()
        if len(self.attempts) >= self.limit:
            self.state = "FAULT_RETRY_LIMIT"
            return
        self.attempts.append(now)
        self.starts += 1
        try:
            # No separate session: TD's process-group shutdown still reaches
            # this worker, and stop() also targets the exact owned child.
            self.worker = self.spawn(self.command, stdin=subprocess.DEVNULL)
            self.state = "RUNNING"
        except OSError:
            self.last_exit = "spawn_failed"
            self.state = "BACKOFF"
            self.next_start = now + min(30, 2 ** min(5, len(self.attempts)))

    def stop(self):
        self.state = "STOPPED"
        if self.worker is not None and self.worker.poll() is None:
            self.worker.terminate()
            try:
                self.worker.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait(timeout=2)
        self.worker = None

    def health(self):
        return {"state": self.state, "worker_pid": self.worker.pid if self.worker else None,
                "starts": self.starts, "last_exit": self.last_exit, "updated_at": time.time()}


def write_health(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-json", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    supervisor = WorkerSupervisor(command)
    parent = os.getppid()
    running = True
    def stop(_signum, _frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running and os.getppid() == parent:
            supervisor.tick(time.monotonic())
            write_health(args.state_json, supervisor.health())
            time.sleep(0.5)
    finally:
        supervisor.stop()
        write_health(args.state_json, supervisor.health())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
