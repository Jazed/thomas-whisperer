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
# Use the venv's interpreter — "$PYTHON" is the system Python and would install
# packages outside the venv that PyInstaller bundles from.
arch -arm64 "$VENV_PY" -m pip install --upgrade pip -q
arch -arm64 "$VENV_PY" -m pip install pyinstaller -r requirements.txt -q

echo "=== Preparing models ==="
python - <<'PYEOF'
import shutil
from pathlib import Path
from huggingface_hub import snapshot_download
import config

engine, repo = config.active_engine()
print(f"  Profile '{config.machine_profile()}' → engine '{engine}' ({repo})")
if engine == "faster_whisper":
    dest = Path("models") / repo
    if not dest.exists():
        src = Path(snapshot_download(f"Systran/faster-whisper-{repo}"))
        dest.parent.mkdir(exist_ok=True)
        shutil.copytree(src, dest, symlinks=False)
    print(f"  '{repo}' bundled.")
else:
    # MLX models live in ~/.cache/huggingface and are loaded at runtime.
    snapshot_download(repo)
    print(f"  '{repo}' cached.")
if config.cleanup_settings().get("enabled"):
    snapshot_download(config.cleanup_settings()["model"])
    print("  Cleanup model cached.")
PYEOF

echo "=== Generating icon ==="
python make_icon.py

echo "=== Cleaning previous build ==="
rm -rf build dist

echo "=== Building arm64 .app ==="
arch -arm64 venv/bin/pyinstaller thomas-whisperer.spec --noconfirm 2>&1

APP="dist/ThomasWhisperer.app"
if [ ! -d "$APP" ]; then
    echo "Build failed — check output above."
    exit 1
fi
built_arch=$(file "$APP/Contents/MacOS/ThomasWhisperer" 2>/dev/null \
    | grep -oE 'universal binary|arm64|x86_64' | head -1 || echo "unknown")
VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$APP/Contents/Info.plist")

# ---------------------------------------------------------------------------
# Sign. With the self-signed identity from ./signing.sh macOS remembers the
# Microphone/Accessibility grants across rebuilds. Without it we fall back to
# ad-hoc signing, which invalidates the grants on every build.
# ---------------------------------------------------------------------------
SIGN_IDENTITY="ThomasWhisperer Self-Signed"
SIGN_KEYCHAIN="$HOME/Library/Keychains/thomaswhisperer-signing.keychain-db"
if [ -f "$SIGN_KEYCHAIN" ]; then
    echo "=== Signing with '$SIGN_IDENTITY' ==="
    security unlock-keychain -p thomaswhisperer "$SIGN_KEYCHAIN"
    # codesign only finds identities in the keychain search list. Add the signing
    # keychain for the duration of the signing step, then restore the original list.
    ORIG_KEYCHAINS=()
    while IFS= read -r kc; do
        kc="${kc#"${kc%%[![:space:]]*}"}"; kc="${kc#\"}"; kc="${kc%\"}"
        [ -n "$kc" ] && ORIG_KEYCHAINS+=("$kc")
    done < <(security list-keychains -d user)
    restore_keychains() { security list-keychains -d user -s "${ORIG_KEYCHAINS[@]}"; }
    trap restore_keychains EXIT
    security list-keychains -d user -s "${ORIG_KEYCHAINS[@]}" "$SIGN_KEYCHAIN"
    codesign --force --deep --keychain "$SIGN_KEYCHAIN" --sign "$SIGN_IDENTITY" "$APP"
    restore_keychains
    trap - EXIT
    STABLE_SIGNATURE=true
else
    echo "=== Signing ad-hoc (run ./signing.sh once to keep permissions across builds) ==="
    codesign --force --deep --sign - "$APP"
    STABLE_SIGNATURE=false
fi
codesign --verify --deep "$APP" && echo "  Signature OK."

# Flush Finder's icon cache so the new icon appears immediately.
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \
    -f "$APP" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Package a DMG for other Macs (drag to Applications + install notes)
# ---------------------------------------------------------------------------
echo "=== Creating DMG ==="
DMG="dist/ThomasWhisperer-$VERSION.dmg"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp INSTALL.txt "$STAGE/Lees mij - installeren.txt"
rm -f "$DMG"
hdiutil create -volname "ThomasWhisperer $VERSION" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
echo "  $DMG ($(du -h "$DMG" | cut -f1))"

echo ""
echo "=== Build complete ==="
echo "  App:  $APP ($built_arch, v$VERSION)"
echo "  DMG:  $DMG"
echo ""

# ---------------------------------------------------------------------------
# Install locally
# ---------------------------------------------------------------------------
echo "=== Installing to /Applications ==="
# Quit the running copy first so the files aren't replaced underneath it.
osascript -e 'tell application id "ai.thomaswhisperer.app" to quit' 2>/dev/null || true
pkill -x ThomasWhisperer 2>/dev/null || true

if ! $STABLE_SIGNATURE; then
    # Ad-hoc: the old grant points at the previous binary hash — clear it so the
    # new build gets a clean prompt instead of a silently broken hotkey.
    tccutil reset Accessibility ai.thomaswhisperer.app >/dev/null 2>&1 || true
fi

# Remove any existing install first — `cp -R src dest` nests src *inside* dest
# when dest already exists, producing /Applications/ThomasWhisperer.app/ThomasWhisperer.app.
rm -rf /Applications/ThomasWhisperer.app
cp -R "$APP" /Applications/ThomasWhisperer.app
xattr -dr com.apple.quarantine /Applications/ThomasWhisperer.app
echo "  Installed."
open /Applications/ThomasWhisperer.app
echo "  Launched. Check the menu bar icon → Microphone/Accessibility status."
