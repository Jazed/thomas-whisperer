# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        ('config.json', '.'),
        ('models/base',  'models/base'),
        ('models/small', 'models/small'),
    ],
    hiddenimports=[
        # PyObjC
        'objc', 'AppKit', 'Foundation', 'Quartz', 'CoreFoundation',
        'ApplicationServices', 'AVFoundation',
        # faster-whisper internals
        'faster_whisper', 'ctranslate2', 'tokenizers', 'huggingface_hub',
        'av', 'tqdm',
        # Audio
        'sounddevice', 'soundfile', '_soundfile', 'cffi', '_cffi_backend',
        # APIs
        'google.generativeai', 'google.ai.generativelanguage_v1beta',
        'anthropic', 'openai',
        # Our modules
        'audio', 'transcribe', 'inject', 'overlay', 'hotkey',
        'dictionary', 'history', 'config', 'app',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'wx', 'matplotlib'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ThomasWhisperer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ThomasWhisperer',
)

app = BUNDLE(
    coll,
    name='ThomasWhisperer.app',
    icon='assets/icon.icns',
    bundle_identifier='ai.thomaswhisperer.app',
    version='1.0.0',
    info_plist={
        'CFBundleName': 'ThomasWhisperer',
        'CFBundleDisplayName': 'ThomasWhisperer',
        'CFBundleShortVersionString': '1.0',
        'NSMicrophoneUsageDescription': 'ThomasWhisperer records your voice for transcription.',
        'NSAccessibilityUsageDescription': 'ThomasWhisperer needs Accessibility access to paste transcribed text.',
        'LSUIElement': True,
        'NSHighResolutionCapable': True,
    },
)
