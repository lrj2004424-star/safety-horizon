"""Windows visible-window ROI capture / 仅采集用户选定窗口的视频区域。"""
import os
import time
from pathlib import PureWindowsPath


def overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _process_name(hwnd):
    import ctypes
    from ctypes import wintypes
    import win32process
    import win32api
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    handle = win32api.OpenProcess(0x1000, False, pid)
    try:
        fn = ctypes.windll.kernel32.QueryFullProcessImageNameW
        fn.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        fn.restype = wintypes.BOOL
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not fn(int(handle), 0, buf, ctypes.byref(size)):
            raise OSError('Unable to confirm window owner')
        return PureWindowsPath(buf.value).name
    finally:
        handle.Close()


def list_windows():
    if os.name != 'nt':
        raise ValueError('Windows window capture is available only on Windows')
    import win32gui
    result = []
    def visit(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title and win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd):
            try:
                process = _process_name(hwnd)
                if 'touchdesigner' not in process.casefold():
                    result.append({'handle': hwnd, 'title': title, 'process': process})
            except OSError:
                pass
            except Exception:
                pass  # Inaccessible system windows are not selectable.
    win32gui.EnumWindows(visit, None)
    return result


class WindowsWindowCapture:
    """Occluded/minimized/off-screen windows fail closed; never grab desktop fallback."""
    def __init__(self, title, process_name, roi, *, fps=20):
        from .live_sources import validate_config
        validate_config({'kind': 'window', 'window_title': title, 'process_name': process_name, 'roi': roi})
        if os.name != 'nt':
            raise ValueError('Windows capture requires Windows')
        import mss
        self.screen = mss.mss()
        self.title, self.process_name, self.roi = title, process_name, roi
        self.fps, self.last_frame, self.sequence = max(1, min(30, fps)), 0.0, 0
        self.width = self.height = 0

    def _region(self):
        import win32gui
        matches = [w for w in list_windows() if w['title'] == self.title and w['process'].casefold() == self.process_name.casefold()]
        if len(matches) != 1:
            raise ValueError('window_missing_or_ambiguous')
        hwnd = matches[0]['handle']
        _, _, width, height = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        a,b,c,d = self.roi
        region = (left+int(width*a), top+int(height*b), left+int(width*c), top+int(height*d))
        if region[2]-region[0] < 64 or region[3]-region[1] < 64:
            raise ValueError('video_roi_too_small')
        monitor = self.screen.monitors[0]
        if not (region[0] >= monitor['left'] and region[1] >= monitor['top'] and
                region[2] <= monitor['left']+monitor['width'] and region[3] <= monitor['top']+monitor['height']):
            raise ValueError('window_outside_screen')
        above, found = [], []
        def inspect(other, _):
            if other == hwnd:
                found.append(True)
            elif not found and win32gui.IsWindowVisible(other) and not win32gui.IsIconic(other):
                above.append(win32gui.GetWindowRect(other))
        win32gui.EnumWindows(inspect, None)
        if not found or any(overlaps(region, rect) for rect in above):
            raise ValueError('video_region_occluded')
        return region

    def read_observation(self):
        from .camera_device import CameraRead
        import numpy as np
        wait = 1 / self.fps - (time.monotonic() - self.last_frame)
        if wait > 0:
            time.sleep(wait)
        self.last_frame = time.monotonic()
        try:
            region = self._region()
            l,t,r,b = region
            frame = np.array(self.screen.grab({'left': l, 'top': t, 'width': r-l, 'height': b-t}))[:, :, :3].copy()
            if self._region() != region:
                raise ValueError('window_moved_during_capture')
            self.width, self.height = r-l, b-t
            self.sequence += 1
            return CameraRead(True, frame, time.monotonic(), self.sequence, 0, 'windows_video_roi_ok')
        except Exception:
            # Do not leak titles/private desktop contents in error logs.
            return CameraRead(False, None, time.monotonic(), self.sequence, 0, 'window_hidden_occluded_or_capture_failed')

    def get(self, prop):
        import cv2
        return {cv2.CAP_PROP_FPS: self.fps, cv2.CAP_PROP_FRAME_WIDTH: self.width,
                cv2.CAP_PROP_FRAME_HEIGHT: self.height}.get(prop, 0)

    def release(self):
        self.screen.close()
