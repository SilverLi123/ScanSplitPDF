"""ScanSplit PDF 程序入口：DPI 感知 + 主窗口（完全离线运行）。"""

from __future__ import annotations

import sys


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def selftest() -> int:
    """构建产物自检：OCR 模型完整性、空白检测、渲染依赖是否可用。"""
    from . import blankdetect
    from .ocr import OcrEngine, ocr_available, verify_models

    ok, issues = verify_models()
    if not ok:
        print("SELFTEST FAIL 模型校验：", "; ".join(issues))
        return 2
    import numpy as np
    from PIL import Image

    engine = OcrEngine.get()
    if not ocr_available():
        print("SELFTEST FAIL OCR 引擎不可用：", engine.error)
        return 2
    # 真正跑一次推理（覆盖检测/识别/方向分类模型的加载）
    engine.recognize(Image.new("RGB", (640, 480), "white"))
    if engine.error:
        print("SELFTEST FAIL OCR 推理：", engine.error)
        return 2

    img = Image.new("RGB", (1240, 1754), "white")
    res = blankdetect.detect_page(np.asarray(img.convert("L")), blankdetect.DetectParams())
    if res.verdict != "blank":
        print("SELFTEST FAIL 空白检测：", res.verdict)
        return 2
    import pypdfium2 as pdfium  # noqa: F401

    print("SELFTEST OK：OCR 模型完整、推理可用、检测与渲染依赖可用")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    _enable_dpi_awareness()
    import tkinter as tk
    from tkinter import ttk

    from .ui import App

    root = tk.Tk()
    root.title("ScanSplit PDF — 扫描件拆分与命名工具")
    root.geometry("1400x820")
    root.minsize(1100, 680)
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
