import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

_HISTORY_DIR  = Path.home() / ".thomas-voice"
_HISTORY_FILE = _HISTORY_DIR / "history.csv"
_COLUMNS      = ["timestamp", "language", "duration_s", "words", "text"]
_lock         = threading.Lock()


def _ensure_file() -> None:
    _HISTORY_DIR.mkdir(exist_ok=True)
    if not _HISTORY_FILE.exists():
        with open(_HISTORY_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_COLUMNS)


def write(text: str, duration_s: float, provider: str, language: str = "") -> None:
    _ensure_file()
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "language":  language,
        "duration_s": round(duration_s, 2),
        "words":     len(text.split()),
        "text":      text,
    }
    with _lock:
        with open(_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_COLUMNS).writerow(row)


def get_history_path() -> str:
    _ensure_file()
    return str(_HISTORY_FILE)
