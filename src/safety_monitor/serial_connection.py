"""Bounded, nonblocking-retry serial output; never guesses among devices."""
from __future__ import annotations

SILENT = b"FATIGUE 2 0 0\n"
UNO_IDS = {(0x2341, 0x0043), (0x2341, 0x0001), (0x2341, 0x0243), (0x2A03, 0x0043)}


def select_uno(ports):
    candidates = sorted({p.device for p in ports if (p.vid, p.pid) in UNO_IDS})
    return candidates[0] if len(candidates) == 1 else None


class SerialConnection:
    def __init__(self, serial_factory, list_ports, *, port=None, baudrate=115200):
        self.factory, self.list_ports = serial_factory, list_ports
        self.fixed_port, self.baudrate = port, baudrate
        self.device = None
        self.port = None
        self.ready = False
        self.reason = "disconnected"
        self.next_attempt = 0.0
        self.ready_at = 0.0
        self.generation = 0

    def tick(self, now):
        if self.device is None and now >= self.next_attempt:
            self.next_attempt = now + 1.0
            self.port = self.fixed_port or select_uno(self.list_ports())
            if not self.port:
                self.reason = "no_unique_supported_uno"
                return False
            try:
                self.device = self.factory(self.port, self.baudrate, timeout=0, write_timeout=0.25)
                self.ready_at = now + 2.0  # UNO may reset on serial open.
                self.reason = "waiting_for_board_reset"
            except OSError:
                self.device = None
                self.reason = "serial_open_failed"
        if self.device is not None and not self.ready and now >= self.ready_at:
            if self.send(SILENT):
                self.ready = True
                self.reason = "connected"
                self.generation += 1
        return self.ready

    def send(self, data):
        if self.device is None:
            return False
        try:
            written = self.device.write(data)
            if written != len(data):
                raise OSError("incomplete serial write")
            return True
        except OSError:
            self.close()
            self.reason = "serial_disconnected"
            return False

    def close(self):
        device, self.device = self.device, None
        self.ready = False
        if device is not None:
            try:
                device.close()
            except OSError:
                pass
