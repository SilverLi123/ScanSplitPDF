# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：ScansplitPDF.exe（onedir，含离线 OCR 模型）。

注意：构建脚本从未在干净机器上验证，若打包结果异常请对照 README 排查。
"""

from pathlib import Path

import rapidocr_onnxruntime

ROOT = Path(SPECPATH)  # noqa: F821  (PyInstaller 注入)

rapid_dir = Path(rapidocr_onnxruntime.__file__).parent

datas = [
    # 离线 OCR 模型与配置（随包分发，运行时绝不联网下载）。
    # 只打包根 config.yaml：子目录的 config.yaml 运行时不会被读取，
    # 且其数据目录在冻结环境中会形成命名空间包、遮蔽真实模块（详见 ocr.py）。
    (str(rapid_dir / "config.yaml"), "rapidocr_onnxruntime"),
    (str(rapid_dir / "models"), "rapidocr_onnxruntime/models"),
    # 模型 SHA-256 完整性清单
    (str(ROOT / "scansplit" / "ocr_model_manifest.json"), "scansplit"),
]

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    # rapidocr 用 importlib.import_module 动态加载子模块（模块名来自 config.yaml），
    # 静态分析发现不了，必须显式声明，否则冻结后 OCR 引擎无法初始化。
    hiddenimports=[
        "rapidocr_onnxruntime.ch_ppocr_v3_det",
        "rapidocr_onnxruntime.ch_ppocr_v3_rec",
        "rapidocr_onnxruntime.ch_ppocr_v2_cls",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter.test", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScanSplitPDF",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ScanSplitPDF",
)
