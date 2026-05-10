import sys
import traceback
import threading
import multiprocessing
import fcntl
from pathlib import Path
import AppKit
from app import AppController

_LOG_FILE = Path.home() / ".thomas-voice" / "app.log"
_LOCK_FD  = None


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


def _acquire_lock() -> bool:
    global _LOCK_FD
    lock_path = Path.home() / ".thomas-voice" / "app.lock"
    lock_path.parent.mkdir(exist_ok=True)
    _LOCK_FD = open(lock_path, "w")
    try:
        fcntl.flock(_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main() -> None:
    _setup_logging()

    if not _acquire_lock():
        print("ThomasWhisperer is already running.", file=sys.stderr)
        sys.exit(0)

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    controller = AppController.alloc().init()
    app.setDelegate_(controller)

    app.run()


if __name__ == "__main__":
    # MUST be first — stops ctranslate2/faster-whisper from spawning infinite copies
    multiprocessing.freeze_support()
    main()
