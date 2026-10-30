# -*- mode: python ; coding: utf-8 -*-
import json
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Bundle faster-whisper model dirs only when that legacy engine is selected.
# MLX models live in ~/.cache/huggingface and are loaded at runtime.
_cfg = json.load(open('config.json'))
_model_datas = [('config.json', '.')]
if _cfg.get('engine') == 'faster_whisper':
    _size = (_cfg.get('engine_models') or {}).get('faster_whisper', 'small')
    _p = Path(f'models/{_size}')
    if _p.exists():
        _model_datas.append((str(_p), f'models/{_size}'))

# mlx-audio / mlx-lm import model code by name at runtime — collect it explicitly.
_dynamic_imports = []
for _pkg in ('mlx_audio.stt.models.parakeet', 'mlx_audio.stt.models.qwen3_asr',
             'mlx_audio.stt.models.cohere_asr', 'mlx_audio.stt.models.voxtral_realtime',
             'mlx_audio.stt.models.whisper', 'mlx_lm.models'):
    _dynamic_imports += collect_submodules(_pkg)
_model_datas += collect_data_files('mlx_audio') + collect_data_files('mlx_lm')

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=_model_datas,
    hiddenimports=[
        # PyObjC
        'objc', 'AppKit', 'Foundation', 'Quartz', 'CoreFoundation',
        'ApplicationServices', 'AVFoundation', 'ServiceManagement',
        # faster-whisper internals
        'faster_whisper', 'ctranslate2', 'tokenizers', 'huggingface_hub',
        'av', 'tqdm',
        # MLX engines + local LLM
        'mlx', 'mlx.core', 'mlx.nn', 'mlx.utils',
        'mlx_whisper', 'mlx_whisper.audio', 'mlx_whisper.decoding',
        'mlx_whisper.load_models', 'mlx_whisper.transcribe', 'mlx_whisper.tokenizer',
        'mlx_audio', 'mlx_audio.stt', 'mlx_audio.stt.utils',
        'mlx_audio.stt.models.parakeet', 'mlx_audio.stt.models.qwen3_asr',
        'mlx_audio.stt.models.cohere_asr', 'mlx_audio.stt.models.voxtral_realtime',
        'mlx_lm', 'transformers',
        # Audio
        'sounddevice', 'soundfile', '_soundfile', 'cffi', '_cffi_backend',
        # APIs
        'google.genai', 'openai',
        # Our modules
        'audio', 'transcribe', 'inject', 'overlay', 'hotkey',
        'dictionary', 'history', 'config', 'app', 'cleanup',
    ] + _dynamic_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # torch/numba/librosa are pulled into the venv by mlx-audio extras but none of
    # the engines we ship import them — excluding saves ~800 MB.
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'wx', 'matplotlib',
              'torch', 'torchaudio', 'torchvision', 'numba', 'llvmlite', 'librosa',
              'tensorflow', 'jax', 'sklearn', 'pandas'],
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
    version='2.0.0',
    info_plist={
        'CFBundleName': 'ThomasWhisperer',
        'CFBundleDisplayName': 'ThomasWhisperer',
        'CFBundleShortVersionString': '2.0.0',
        'NSMicrophoneUsageDescription': 'ThomasWhisperer records your voice for transcription.',
        'NSAccessibilityUsageDescription': 'ThomasWhisperer needs Accessibility access to paste transcribed text.',
        'LSUIElement': True,
        'LSMinimumSystemVersion': '14.0',
        'NSHighResolutionCapable': True,
    },
)
