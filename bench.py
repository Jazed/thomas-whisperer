"""Compare transcription engines on your own recordings.

    python bench.py ~/.thomas-voice/recordings
    python bench.py <dir> --engines parakeet,qwen3_asr,whisper
    python bench.py <dir> --engines qwen3_asr=mlx-community/Qwen3-ASR-1.7B-8bit

Every engine runs in its own subprocess so peak memory is measured per model
and an 8 GB machine never holds two models at once. If a .txt with the same
name sits next to a .wav, word error rate (WER) is computed against it.
"""
import re
import sys
import json
import time
import argparse
import subprocess
from pathlib import Path

DEFAULT_ENGINES = "parakeet,qwen3_asr,whisper"


def _norm(s: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", s.lower()).split()


def wer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d = d, [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (rw != hw))
    return d[-1] / max(len(r), 1)


def run_one(engine: str, repo: str | None, folder: Path) -> dict:
    """Load one engine and transcribe every .wav in folder (runs in a subprocess)."""
    import resource
    import config
    config.cfg.api_provider = "local"
    config.cfg.engine = engine
    if repo:
        config.cfg.engine_models = {**(getattr(config.cfg, "engine_models", {}) or {}), engine: repo}
    import transcribe

    t0 = time.monotonic()
    transcribe.warm_up()
    load_s = time.monotonic() - t0

    rows = []
    for wav in sorted(folder.glob("*.wav")):
        audio = wav.read_bytes()
        t0 = time.monotonic()
        text, lang = transcribe.transcribe(audio)
        dt = time.monotonic() - t0
        ref = wav.with_suffix(".txt")
        rows.append({
            "file": wav.name, "seconds": dt, "lang": lang, "text": text,
            "audio_s": len(transcribe._wav_to_float(audio)) / 16000,
            "wer": wer(ref.read_text(), text) if ref.exists() else None,
        })
    return {
        "engine": engine, "repo": config.active_engine()[1], "load_s": load_s,
        "peak_gb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9,
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--engines", default=DEFAULT_ENGINES,
                    help="comma-separated engine[=repo] list")
    ap.add_argument("--verbose", "-v", action="store_true", help="print every transcript")
    ap.add_argument("--_one", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._one:
        engine, _, repo = args._one.partition("=")
        print("BENCH_JSON " + json.dumps(run_one(engine, repo or None, args.folder)))
        return

    wavs = list(args.folder.glob("*.wav"))
    if not wavs:
        sys.exit(f"No .wav files in {args.folder}")
    print(f"{len(wavs)} recordings in {args.folder}\n")

    results = []
    for spec in args.engines.split(","):
        print(f"→ {spec} …", flush=True)
        proc = subprocess.run(
            [sys.executable, __file__, str(args.folder), "--_one", spec],
            capture_output=True, text=True,
        )
        line = next((l for l in proc.stdout.splitlines() if l.startswith("BENCH_JSON ")), None)
        if not line:
            tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
            print(f"  FAILED\n  {tail}\n")
            continue
        res = json.loads(line[len("BENCH_JSON "):])
        results.append(res)
        if args.verbose:
            for r in res["rows"]:
                w = f"wer={r['wer']:.2f}" if r["wer"] is not None else ""
                print(f"  {r['file']:24s} {r['seconds']:5.2f}s {r['lang'] or '--':3s} {w:9s} {r['text']}")

    print(f"\n{'engine':12s} {'load':>6s} {'avg':>6s} {'RTF':>6s} {'WER':>6s} {'peak':>7s}  repo")
    for res in results:
        rows = res["rows"][1:] or res["rows"]   # first file includes kernel compile
        avg  = sum(r["seconds"] for r in rows) / len(rows)
        rtf  = sum(r["seconds"] for r in rows) / max(sum(r["audio_s"] for r in rows), 1e-6)
        wers = [r["wer"] for r in res["rows"] if r["wer"] is not None]
        w    = f"{sum(wers) / len(wers):.3f}" if wers else "  -  "
        print(f"{res['engine']:12s} {res['load_s']:5.1f}s {avg:5.2f}s {rtf:6.3f} {w:>6s} "
              f"{res['peak_gb']:5.2f}GB  {res['repo']}")


if __name__ == "__main__":
    main()
