"""Standard-library OS seams. No state directories are created on import.

Lock the event file itself, including empty files and future appended bytes.
Windows readers must take a shared lock because its byte locks are mandatory.
"""
from contextlib import contextmanager
import errno
import math
import os
import threading
import time

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_OVERLAPPED)]
    _kernel.LockFileEx.restype = wintypes.BOOL
    _kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.POINTER(_OVERLAPPED)]
    _kernel.UnlockFileEx.restype = wintypes.BOOL
    _kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel.WaitForSingleObject.restype = wintypes.DWORD
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
else:
    import fcntl


DEFAULT_LOCK_TIMEOUT = 30.0
_thread_guard = threading.Lock()
_thread_locks = {}


def _remaining(deadline):
    return None if deadline is None else max(0.0, deadline - time.monotonic())


@contextmanager
def file_lock(file, *, exclusive=True, timeout=DEFAULT_LOCK_TIMEOUT):
    """Bounded, non-reentrant lock; acquisition/unlock errors always propagate.

    Each caller must keep its file open and flush writes inside this context.
    A per-inode thread mutex also serializes callers sharing an OS handle.
    timeout=None explicitly requests an unbounded wait; zero tries once.
    """
    if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
        raise ValueError("lock timeout must be finite and nonnegative, or None")
    deadline = None if timeout is None else time.monotonic() + timeout
    fd = file.fileno()
    st = os.fstat(fd)
    key = (st.st_dev, st.st_ino)
    with _thread_guard:
        entry = _thread_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    local_acquired = False
    os_acquired = False
    try:
        remaining = _remaining(deadline)
        local_acquired = (entry[0].acquire() if remaining is None
                          else entry[0].acquire(timeout=remaining))
        if not local_acquired:
            raise TimeoutError("timed out waiting for event-file thread lock")
        if os.name == "nt":
            handle = msvcrt.get_osfhandle(fd)
            overlap = _OVERLAPPED()  # offset zero, independent of append position
        while True:
            if os.name == "nt":
                flags = 0x1 | (0x2 if exclusive else 0)  # FAIL_IMMEDIATELY / EXCLUSIVE
                # 2**64-1 bytes covers empty files, long files and future growth.
                if _kernel.LockFileEx(handle, flags, 0, 0xffffffff, 0xffffffff,
                                      ctypes.byref(overlap)):
                    os_acquired = True
                else:
                    error = ctypes.get_last_error()
                    if error != 33:  # ERROR_LOCK_VIOLATION is contention, all else is fatal
                        raise ctypes.WinError(error)
            else:
                try:
                    fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                                | fcntl.LOCK_NB)
                    os_acquired = True
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EINTR):
                        raise
            if os_acquired:
                break
            remaining = _remaining(deadline)
            if remaining is not None and remaining <= 0:
                raise TimeoutError("timed out waiting for event-file OS lock")
            time.sleep(0.01 if remaining is None else min(0.01, remaining))
        yield file
    finally:
        try:
            if os_acquired:
                if os.name == "nt":
                    if not _kernel.UnlockFileEx(handle, 0, 0xffffffff, 0xffffffff,
                                                ctypes.byref(overlap)):
                        raise ctypes.WinError(ctypes.get_last_error())
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            if local_acquired:
                entry[0].release()
            with _thread_guard:
                entry[1] -= 1
                if not entry[1]:
                    del _thread_locks[key]


def pid_alive(pid):
    """Read-only probe. Never send a Windows signal (including signal zero).

    Windows access-denied is conservatively alive, avoiding takeover of a
    protected process. Unexpected API errors are surfaced, not treated as dead.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError, OverflowError):
        return False
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except (OSError, OverflowError):
            return False
    if pid > 0xffffffff:
        return False
    handle = _kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER (nonexistent PID)
            return False
        if error == 5:  # ERROR_ACCESS_DENIED: existence cannot be disproved
            return True
        raise ctypes.WinError(error)
    try:
        result = _kernel.WaitForSingleObject(handle, 0)
        if result == 0x102:  # WAIT_TIMEOUT: process has not exited
            return True
        if result == 0:  # WAIT_OBJECT_0: exited, even if exit code is STILL_ACTIVE
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not _kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def replace_file(source, destination, *, timeout=2.0):
    """Retry short Windows reader/antivirus sharing races, then raise the error.

    Python's ordinary readers do not grant FILE_SHARE_DELETE on Windows, so a
    destination open for a brief read can temporarily prevent atomic replacement.
    POSIX and non-sharing IO errors retain their immediate failure behavior.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if (os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33)
                    or time.monotonic() >= deadline):
                raise
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))


def _serve(data_dir, port):
    """Scheduled-task foreground action: this PID is the server, no child tree."""
    import runpy
    import sys
    import traceback

    data_dir = os.path.abspath(os.path.expanduser(data_dir))
    os.environ["RB_DATA_DIR"] = data_dir
    os.environ["RB_PORT"] = str(port)
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, root)
    # Append raw UTF-8 directly; PowerShell 5.1's UTF-16 redirection is bypassed.
    with open(os.path.join(log_dir, "windows-server.log"), "a", encoding="utf-8",
              buffering=1, newline="\n") as log:
        original_out, original_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        try:
            print("%s start pid=%d port=%d" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                               os.getpid(), port), flush=True)
            runpy.run_path(os.path.join(root, "server.py"), run_name="__main__")
        except Exception:
            traceback.print_exc()
            return 1
        finally:
            sys.stdout, sys.stderr = original_out, original_err
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="review-board service action")
    parser.add_argument("action", choices=["serve"])
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=8798)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    raise SystemExit(_serve(args.data_dir, args.port))
