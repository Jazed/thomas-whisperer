"""
py2app build script — creates a distributable .app bundle.

Usage:
    source venv/bin/activate
    pip install py2app
    python setup.py py2app

Output: dist/ThomasVoice.app
"""
from setuptools import setup

APP = ["main.py"]
DATA_FILES = [
    ("", ["config.json"]),
]
OPTIONS = {
    "iconfile": "assets/icon.icns",
    "plist": {
        "CFBundleName": "ThomasVoice",
        "CFBundleDisplayName": "ThomasVoice",
        "CFBundleIdentifier": "ai.thomasvoice.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0",
        # Required privacy descriptions — macOS shows these to the user
        "NSMicrophoneUsageDescription": (
            "ThomasVoice needs microphone access to record your voice for transcription."
        ),
        # Accessibility is granted via System Settings, not Info.plist
        # Run as an agent (no Dock icon)
        "LSUIElement": True,
        "LSBackgroundOnly": False,
    },
    "packages": [
        "AppKit",
        "Foundation",
        "Quartz",
        "CoreFoundation",
        "ApplicationServices",
        "sounddevice",
        "numpy",
        "scipy",
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "google.generativeai",
        "openai",
        "anthropic",
    ],
    "excludes": ["tkinter", "PyQt5", "PyQt6"],
    "semi_standalone": False,
    "site_packages": True,
    "argv_emulation": False,
}

setup(
    app=APP,
    name="ThomasVoice",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
