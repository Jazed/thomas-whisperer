import sys
import os
import signal
import traceback
import threading
import multiprocessing
import time
from pathlib import Path
import AppKit
from app import AppController

_LOG_FILE  = Path.home() / ".thomas-voice" / "app.log"
_PID_FILE  = Path.home() / ".thomas-voice" / "app.pid"


def _setup_logging() -> None:
    """
    - Tee stderr → app.log so every print(..., file=sys.stderr) is captured.
    - Hook sys.excepthook and threading.excepthook so all crashes land in app.log.
    """
    _LOG_FILE.parent.mkdir(exist_ok=True)
    log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)

    class _Tee:
        def __init__(self, *streams):
            self._s = streams
        def write(self, data):
            for s in self._s:
                try: s.write(data)
                except Exception: pass
        def flush(self):
            for s in self._s:
                try: s.flush()
                except Exception: pass
        def fileno(self):
            return self._s[0].fileno()

    sys.stderr = _Tee(sys.__stderr__, log_fh)

    def _excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log_fh.write(f"\n[CRASH] {tb}\n")
        log_fh.flush()
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        tb = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        log_fh.write(f"\n[THREAD CRASH] thread={args.thread}\n{tb}\n")
        log_fh.flush()

    threading.excepthook = _thread_excepthook


def _kill_existing() -> None:
    """Kill all other python processes running main.py, then write our PID."""
    import subprocess
    _PID_FILE.parent.mkdir(exist_ok=True)
    my_pid = os.getpid()

    # Find all other python processes running main.py
    try:
        result = subprocess.run(
            ["pgrep", "-if", "python.*main\\.py"],  # -i: case-insensitive (macOS Python = capital P)
            capture_output=True, text=True,
        )
        pids = [int(p) for p in result.stdout.split() if p.strip() and int(p) != my_pid]
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        if pids:
            time.sleep(1.0)
    except Exception:
        pass

    _PID_FILE.write_text(str(my_pid))


def _cleanup_pid() -> None:
    try:
        if _PID_FILE.exists() and int(_PID_FILE.read_text()) == os.getpid():
            _PID_FILE.unlink()
    except Exception:
        pass


def main() -> None:
    _setup_logging()
    _kill_existing()

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
