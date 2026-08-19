# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH)
winrt_datas, winrt_binaries, winrt_hidden = collect_all("winrt")

a = Analysis(
    [str(root / "WMNavigation.py")],
    pathex=[str(root / "src")],
    binaries=winrt_binaries,
    datas=[
        (str(root / "assets"), "assets"),
        (str(root / "data" / "maps.json"), "data"),
        (str(root / "data" / "questie"), "data/questie"),
        *winrt_datas,
    ],
    hiddenimports=[
        "PySide6.QtSvg",
        "paho",
        "paho.mqtt",
        "paho.mqtt.client",
        "cv2",
        "numpy",
        "soundcard",
        "UnityPy",
        "texture2ddecoder",
        "etcpak",
        "lz4",
        "brotli",
        "astc_encoder_py",
        "tpk_ar",
        *collect_submodules("UnityPy"),
        *collect_submodules("winrt"),
        *winrt_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "onnxruntime",
        "pandas",
        "matplotlib",
        "scipy",
        "IPython",
        "jupyter",
        "transformers",
        "huggingface_hub",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WMNavigation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "assets" / "icon.png"),
)
