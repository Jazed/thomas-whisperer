"""Fetch models up front so the overlay can say what is happening.

The model libraries download inside their load functions without a usable
progress hook. ensure() downloads the same files first and measures the bytes
landing in the Hugging Face cache, so the loaders then find everything cached.
"""
import sys
import threading
from pathlib import Path

# Called as listener(text, fraction) — fraction is 0..1 while downloading, else None.
listener = None


def report(text: str | None, fraction: float | None = None) -> None:
    if listener:
        try:
            listener(text, fraction)
        except Exception as e:
            print(f"[downloads] status listener failed: {e}", file=sys.stderr)


def _dir_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass   # file moved or removed mid-scan
    return total


def _mb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def ensure(repo: str, name: str, allow_patterns: list | None = None) -> None:
    """Download `repo` into the HF cache unless it is already complete there."""
    if "/" not in repo or Path(repo).expanduser().exists():
        return   # local path or a faster-whisper size name — nothing to fetch here
    from huggingface_hub import HfApi, constants, snapshot_download
    from huggingface_hub.utils import filter_repo_objects

    try:
        snapshot_download(repo, allow_patterns=allow_patterns, local_files_only=True)
        return
    except Exception:
        pass   # not (fully) cached yet

    report(f"Checking {name}…")
    info  = HfApi().model_info(repo, files_metadata=True)
    sizes = {s.rfilename: s.size or 0 for s in info.siblings}
    total = sum(sizes[f] for f in filter_repo_objects(sizes, allow_patterns=allow_patterns)) or 1

    # Finished files land in the repo folder; Xet stages chunks in the shared blobs folder.
    cache    = Path(constants.HF_HUB_CACHE)
    watched  = [cache / ("models--" + repo.replace("/", "--")), cache / "blobs"]
    failure  = []

    def _download():
        try:
            snapshot_download(repo, allow_patterns=allow_patterns)
        except Exception as e:
            failure.append(e)

    print(f"[downloads] Downloading {repo} ({_mb(total)})…", file=sys.stderr)
    worker = threading.Thread(target=_download, daemon=True)
    worker.start()
    while worker.is_alive():
        done = min(sum(_dir_bytes(p) for p in watched if p.exists()), total)
        report(f"Downloading {name}… {100 * done // total}% · {_mb(done)} / {_mb(total)}",
               done / total)
        worker.join(0.5)
    if failure:
        raise failure[0]
    print(f"[downloads] {repo} downloaded.", file=sys.stderr)
