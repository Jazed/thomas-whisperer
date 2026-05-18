# -*- mode: python ; coding: utf-8 -*-
import json
import sys
from pathlib import Path

block_cipher = None

# Bundle faster-whisper model dirs only when using the local provider.
# MLX models live in ~/.cache/huggingface and are loaded at runtime.
_cfg = json.load(open('config.json'))
_provider = _cfg.get('api_provider', 'local')
_model_datas = [('config.json', '.')]
if _provider == 'local':
    _sizes = set((_cfg.get('language_models') or {}).values()) | {_cfg.get('local_whisper_model', 'base')}
    for _m in _sizes:
        _p = Path(f'models/{_m}')
        if _p.exists():
            _model_datas.append((str(_p), f'models/{_m}'))

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=_model_datas,
    hiddenimports=[
        # PyObjC
        'objc', 'AppKit', 'Foundation', 'Quartz', 'CoreFoundation',
        'ApplicationServices', 'AVFoundation',
        # faster-whisper internals
        'faster_whisper', 'ctranslate2', 'tokenizers', 'huggingface_hub',
        'av', 'tqdm',
        # MLX Whisper
        'mlx', 'mlx.core', 'mlx.nn', 'mlx.utils',
        'mlx_whisper', 'mlx_whisper.audio', 'mlx_whisper.decoding',
        'mlx_whisper.load_models', 'mlx_whisper.transcribe', 'mlx_whisper.tokenizer',
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
