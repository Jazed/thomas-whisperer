import sys
import os
import signal
import traceback
import threading
import multiprocessing
import time
from datetime import datetime
from pathlib import Path
import AppKit
from app import AppController

_LOG_FILE  = Path.home() / ".thomas-voice" / "app.log"
_PID_FILE  = Path.home() / ".thomas-voice" / "app.pid"

_LOG_MAX_BYTES = 512 * 1024   # rotate at 500 KB
_LOG_BACKUPS   = 3            # keep app.log.1 / .2 / .3


def _setup_logging() -> None:
    """
    Tee stderr → app.log (rotating, timestamped).
    Every print(..., file=sys.stderr) call gets a full datetime prefix and lands
    in the log file.  Crashes via sys/threading.excepthook are captured too.
    Log rotates at 500 KB; three backups are kept (app.log.1 / .2 / .3).
    """
    _LOG_FILE.parent.mkdir(exist_ok=True)

    class _Tee:
        """Line-buffered tee: adds timestamps, writes to terminal + rotating file."""

        def __init__(self):
            self._buf     = ""
            self._written = _LOG_FILE.stat().st_size if _LOG_FILE.exists() else 0
            self._fh      = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)

        # ---- internal helpers -----------------------------------------------

        def _emit(self, line: str) -> None:
            ts      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stamped = f"{ts}  {line}\n"
            try:
                sys.__stderr__.write(stamped)
                sys.__stderr__.flush()
            except Exception:
                pass
            try:
                self._fh.write(stamped)
                self._fh.flush()
                self._written += len(stamped.encode("utf-8"))
                if self._written >= _LOG_MAX_BYTES:
                    self._rotate()
            except Exception:
                pass

        def _rotate(self) -> None:
            try:
                self._fh.close()
                for i in range(_LOG_BACKUPS - 1, 0, -1):
                    src = Path(f"{_LOG_FILE}.{i}")
                    dst = Path(f"{_LOG_FILE}.{i + 1}")
                    if src.exists():
                        src.rename(dst)
                if _LOG_FILE.exists():
                    _LOG_FILE.rename(Path(f"{_LOG_FILE}.1"))
                self._fh      = open(_LOG_FILE, "w", encoding="utf-8", buffering=1)
                self._written = 0
                self._emit("[startup] Log rotated.")
            except Exception:
                pass

        # ---- public interface (file-like) ------------------------------------

        def write(self, data: str) -> None:
            self._buf += data
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():   # skip blank lines
                    self._emit(line)

        def flush(self) -> None:
            # Flush any partial line that never got a newline
            if self._buf.strip():
                self._emit(self._buf)
                self._buf = ""
            try: sys.__stderr__.flush()
            except Exception: pass
            try: self._fh.flush()
            except Exception: pass

        def fileno(self) -> int:
            return sys.__stderr__.fileno()

    sys.stderr = _Tee()

    # Session separator — visible even after many rotations
    print(f"[startup] {'=' * 56}", file=sys.stderr)
    print(f"[startup] Thomas Whisperer started  pid={os.getpid()}", file=sys.stderr)
    print(f"[startup] {'=' * 56}", file=sys.stderr)

    def _excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(f"[CRASH] {tb}", file=sys.stderr)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        tb = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        print(f"[THREAD CRASH] thread={args.thread}\n{tb}", file=sys.stderr)

    threading.excepthook = _thread_excepthook


_LOCK_FILE = Path.home() / ".thomas-voice" / "app.lock"
_lock_fh   = None   # held open for the lifetime of the process


def _take_over_instance() -> None:
    """Single instance: the newest launch wins.

    An exclusive flock on app.lock marks the running instance. If another process
    holds it, we SIGTERM exactly that PID (read from app.pid) and wait for the lock.
    Works for both `python main.py` and the frozen .app, and never touches
    unrelated processes."""
    import fcntl
    global _lock_fh
    _LOCK_FILE.parent.mkdir(exist_ok=True)
    _lock_fh = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            old_pid = int(_PID_FILE.read_text())
            print(f"[startup] Replacing running instance pid={old_pid}", file=sys.stderr)
            os.kill(old_pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass
        deadline = time.monotonic() + 5.0
        while True:
            try:
                fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    print("[startup] Another instance is still running — exiting.", file=sys.stderr)
                    sys.exit(0)
                time.sleep(0.1)
    _PID_FILE.write_text(str(os.getpid()))


def _cleanup_pid() -> None:
    try:
        if _PID_FILE.exists() and int(_PID_FILE.read_text()) == os.getpid():
            _PID_FILE.unlink()
    except Exception:
        pass


def main() -> None:
    _setup_logging()
    _take_over_instance()

    import atexit
    atexit.register(_cleanup_pid)

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    controller = AppController.alloc().init()
    app.setDelegate_(controller)

    app.run()


if __name__ == "__main__":
    # MUST be first — stops ctranslate2/faster-whisper from spawning infinite copies
    multiprocessing.freeze_support()
    main()
