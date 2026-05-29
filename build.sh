#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Pick the best Python available: prefer universal2/arm64 over Intel-only
# ---------------------------------------------------------------------------
pick_python() {
    # Python.org universal2 install (best choice)
    local p="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11"
    [ -f "$p" ] && echo "$p" && return
    # Native arm64 Homebrew
    p="/opt/homebrew/bin/python3.11"
    [ -f "$p" ] && echo "$p" && return
    # Fallback: Intel Homebrew (works via Rosetta 2 but builds x86_64)
    p="/usr/local/bin/python3.11"
    [ -f "$p" ] && echo "$p" && return
    echo "python3"
}

PYTHON=$(pick_python)
ARCH_INFO=$(file "$PYTHON" 2>/dev/null | grep -oE 'universal binary|arm64|x86_64' | head -1 || echo "unknown")
echo "=== Using Python: $PYTHON ($ARCH_INFO) ==="

if echo "$ARCH_INFO" | grep -q "x86_64" && ! echo "$ARCH_INFO" | grep -q "universal"; then
    echo ""
    echo "  WARNING: Building with Intel-only Python → app will be x86_64 (Rosetta 2 on Apple Silicon)"
    echo "  For a native arm64 build, install Python.org universal2 Python first:"
    echo "    1. Download: https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg"
    echo "    2. Install:  sudo installer -pkg ~/Downloads/python-3.11.9-macos11.pkg -target /"
    echo "    3. Re-run this script."
    echo ""
fi

# ---------------------------------------------------------------------------
# Create / recreate venv if it's not using the right Python
# ---------------------------------------------------------------------------
VENV_PY="$SCRIPT_DIR/venv/bin/python3"
needs_venv=true
if [ -f "$VENV_PY" ]; then
    venv_bin=$(python3 -c "import os; print(os.path.realpath('$VENV_PY'))" 2>/dev/null || true)
    if [ "$(file "$venv_bin" 2>/dev/null | grep -oE 'universal binary|arm64' | head -1)" = \
         "$(file "$PYTHON"  2>/dev/null | grep -oE 'universal binary|arm64' | head -1)" ]; then
        needs_venv=false
    fi
fi

if $needs_venv; then
    echo "=== Creating venv ==="
    rm -rf "$SCRIPT_DIR/venv"
    "$PYTHON" -m venv "$SCRIPT_DIR/venv"
fi

source "$SCRIPT_DIR/venv/bin/activate"

echo "=== Installing / updating dependencies ==="
arch -arm64 "$PYTHON" -m pip install --upgrade pip -q
arch -arm64 "$PYTHON" -m pip install pyinstaller faster-whisper sounddevice numpy \
    pyobjc-core pyobjc-framework-Cocoa \
    pyobjc-framework-Quartz pyobjc-framework-ApplicationServices \
    mlx-whisper \
    -q

echo "=== Preparing models ==="
python - <<'PYEOF'
import json, shutil, sys
from pathlib import Path

cfg      = json.load(open("config.json"))
provider = cfg.get("api_provider", "local")

if provider == "mlx":
    model = cfg.get("mlx_whisper_model", "mlx-community/whisper-large-v3-turbo")
    from huggingface_hub import snapshot_download
    cache = Path(snapshot_download(model))
    print(f"  MLX model '{model}' ready ({sum(f.stat().st_size for f in cache.rglob('*') if f.is_file()) // 1_000_000} MB cached).")
else:
    lang_models = cfg.get("language_models") or {}
    sizes = set(lang_models.values()) | {cfg.get("local_whisper_model", "base")}
    for size in sizes:
        dest = Path("models") / size
        if dest.exists():
            print(f"  '{size}' already bundled — skipping.")
            continue
        print(f"  Downloading '{size}' from HuggingFace…")
        from huggingface_hub import snapshot_download
        src = Path(snapshot_download(f"Systran/faster-whisper-{size}"))
        dest.parent.mkdir(exist_ok=True)
        shutil.copytree(src, dest, symlinks=False)
        print(f"  '{size}' bundled ({sum(f.stat().st_size for f in dest.rglob('*') if f.is_file()) // 1_000_000} MB).")
PYEOF

echo "=== Generating icon ==="
python make_icon.py

echo "=== Cleaning previous build ==="
rm -rf build dist

echo "=== Building arm64 .app ==="
arch -arm64 venv/bin/pyinstaller thomas-whisperer.spec --noconfirm 2>&1

if [ -d "dist/ThomasWhisperer.app" ]; then
    built_arch=$(file dist/ThomasWhisperer.app/Contents/MacOS/ThomasWhisperer 2>/dev/null \
        | grep -oE 'universal binary|arm64|x86_64' | head -1 || echo "unknown")

    # Flush Finder's icon cache so the new icon appears immediately.
    touch dist/ThomasWhisperer.app
    /System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \
        -f dist/ThomasWhisperer.app 2>/dev/null || true

    # Clear the stale Accessibility TCC entry so the new binary gets a clean prompt.
    # The per-user TCC database doesn't need sudo.
    echo "=== Resetting Accessibility permission for new binary ==="
    if tccutil reset Accessibility ai.thomaswhisperer.app 2>/dev/null; then
        echo "  Old Accessibility entry cleared — app will prompt once on next launch."
    else
        echo "  No previous Accessibility entry found (fresh install)."
    fi

    echo ""
    echo "=== Build complete ==="
    echo "  App:  dist/ThomasWhisperer.app"
    echo "  Arch: $built_arch"
    echo ""
    echo "  To share:   zip -r ThomasWhisperer.zip dist/ThomasWhisperer.app"
    echo ""

    echo "=== Installing to /Applications ==="
    # Remove any existing install first — `cp -r src dest` nests src *inside* dest
    # when dest already exists, producing /Applications/ThomasWhisperer.app/ThomasWhisperer.app.
    rm -rf /Applications/ThomasWhisperer.app
    cp -R dist/ThomasWhisperer.app /Applications/ThomasWhisperer.app
    xattr -dr com.apple.quarantine /Applications/ThomasWhisperer.app
    echo "  Installed — quarantine flag removed, Gatekeeper will not block it."
    echo ""
    echo "  Grant Microphone + Accessibility when prompted on first launch."
else
    echo "Build failed — check output above."
    exit 1
fi
