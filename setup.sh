#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== thomas-voice setup ==="

# Check Python version
PYTHON=$(command -v python3.11 || command -v python3.12 || command -v python3.10 || command -v python3)
if [ -z "$PYTHON" ]; then
  echo "ERROR: python3 not found. Install from https://www.python.org/downloads/"
  exit 1
fi
echo "Using Python: $($PYTHON --version)"

# Create venv
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  $PYTHON -m venv venv
fi
source venv/bin/activate

# Install dependencies
echo "Installing dependencies (this may take a minute)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ""
echo "=== Verifying install ==="
python3 -c "from AppKit import NSPanel; print('  PyObjC (AppKit): OK')"
python3 -c "from Quartz import CGEventTapCreate; print('  PyObjC (Quartz): OK')"
python3 -c "import sounddevice; print('  sounddevice: OK')"
python3 -c "from faster_whisper import WhisperModel; print('  faster-whisper: OK')"

echo ""
echo "=== Permissions checklist ==="
echo "  Before running, grant these in System Settings > Privacy & Security:"
echo "  1. Microphone       — required for audio recording"
echo "  2. Accessibility    — required for global hotkey and text injection"
echo "  (The app will prompt you on first launch)"
echo ""
echo "=== Setup complete ==="
echo "Run the app with:"
echo "  source venv/bin/activate && python3 main.py"
