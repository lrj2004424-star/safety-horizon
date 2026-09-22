"""Cross-platform file locks and owned child processes / 跨平台互斥与进程回收。"""
import errno
import os
import signal
import subprocess
import time


def lock_file(handle, *, blocking=True):
    if os.name != 'nt':
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import msvcrt
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if not blocking:
                raise BlockingIOError('Another instance owns this lock') from exc
            time.sleep(.05)


def unlock_file(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class OwnedJob:
    """Windows closes all owned descendants even if the launcher is killed."""
    def __init__(self):
        self.handle = None
        if os.name == 'nt':
            import win32job
            # A unique name works with pywin32 builds that reject a null name.
            import uuid
            self.handle = win32job.CreateJobObject(None, 'SafetyHorizon-' + uuid.uuid4().hex)
            info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
            info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)

    def attach(self, process):
        if self.handle is not None:
            import win32job
            win32job.AssignProcessToJobObject(self.handle, int(process._handle))

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


def stop_child(process, timeout=6):
    if process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
