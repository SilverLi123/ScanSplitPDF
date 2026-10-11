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
    # Exercise the shipped OCR/render/export stack with an actual Chinese scan.
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from PIL import ImageDraw, ImageFont
    from pypdf import PdfReader
    from . import ocrfields, exporter, verify
    from .model import ScanDocument, Group
    from .render import PageRenderer

    with TemporaryDirectory(prefix="scansplit-selftest-") as folder:
        folder = Path(folder)
        scan = Image.new("RGB", (1200,1600), "white")
        draw = ImageDraw.Draw(scan)
        font_path = Path("C:/Windows/Fonts/msyh.ttc")
        draw.text((240,230), "教学设备统计表", font=ImageFont.truetype(str(font_path),48), fill="black")
        draw.text((80,360), "填报单位：教务处", font=ImageFont.truetype(str(font_path),36), fill="black")
        source = folder/"source.pdf"
        scan.save(source,"PDF",resolution=144)
        doc = ScanDocument(source,PdfReader(str(source)))
        renderer = PageRenderer(source)
        try:
            rendered = renderer.render(0,width_px=1200)
            candidates = ocrfields.extract_reporting_unit(engine.recognize(rendered), *rendered.size)
            if not candidates or candidates[0].text != "教务处":
                print("SELFTEST FAIL 中文 OCR/填报单位提取")
                return 2
        finally:
            renderer.close()
        doc.groups=[Group(1,1,"教学设备统计表","教务处")]
        report=exporter.export_documents([doc],exporter.ExportOptions(out_dir=folder/"export"))
        if len(report.succeeded)!=1 or report.failed or report.coverage_issues or not report.manifest_path:
            print("SELFTEST FAIL 无损导出/页面记录")
            return 2
        if verify.file_sha256(source)!=doc.source_sha256:
            print("SELFTEST FAIL 源文件发生变化")
            return 2

    print("SELFTEST OK：模型完整、中文 OCR、填报单位提取、渲染、无损导出和页面记录可用")
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
