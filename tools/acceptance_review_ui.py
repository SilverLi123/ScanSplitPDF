"""Capture only this app's synthetic review window for bounded native UI QA."""
import argparse
import json
import time
import tkinter as tk
import ctypes
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageGrab
from pypdf import PdfWriter

from scansplit import settings, verify
from scansplit.main import _enable_dpi_awareness
from scansplit.model import BlankResult, Group
from scansplit.review_ui import ReviewDialog
from scansplit.ui import App
from tests.ocrimg import make_cn_document
from tests.samplegen import _add_image_page


def run(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    source = out / "synthetic-review.pdf"
    writer = PdfWriter()
    for page in range(1, 7):
        image = (Image.new("RGB", (1400, 1980), "white") if page == 2 else
                 make_cn_document("教学设备统计表" if page < 5 else "人员情况说明",
                                  [f"原始扫描第 {page} 页", "此文件仅用于审核窗口演示。",
                                   "请检查各页是否属于同一份文件。"], unit="教务处"))
        _add_image_page(writer, image)
    with source.open("wb") as stream:
        writer.write(stream)
    before_sha = verify.file_sha256(source)
    _enable_dpi_awareness()
    settings.load_settings = lambda: {}
    settings.save_settings = lambda _: None
    root = tk.Tk()
    root.geometry("1100x700+20+20")
    app = App(root)
    root.update()
    dialog = None
    captures = []

    def shot(name):
        dialog.attributes("-topmost", True)
        dialog.lift()
        for _ in range(5):
            root.update()
            time.sleep(.08)
        assert dialog.winfo_viewable()
        api = ctypes.WinDLL("user32",use_last_error=True)
        api.GetParent.argtypes=[wintypes.HWND]
        api.GetParent.restype=wintypes.HWND
        # Print only our HWND; a screen rectangle can accidentally capture an overlapping app.
        image=ImageGrab.grab(window=api.GetParent(dialog.winfo_id()))
        image.save(out/name)
        assert dialog.actions.winfo_y()+dialog.actions.winfo_height() <= dialog.winfo_height()
        captures.append(dict(file=name, geometry=dialog.geometry(), mode=dialog.mode,
                             original_page=dialog.browser_number, review_target=dialog.page(),
                             canvas_size=[dialog.browser_page.canvas.winfo_width(),
                                          dialog.browser_page.canvas.winfo_height()]))

    try:
        app.import_files([str(source.resolve())])
        doc = app._active_doc()
        doc.analysis_complete = True
        doc.groups = [Group(1, 4, excluded_pages=(2,)), Group(5, 6)]
        doc.pages[1].blank = BlankResult("blank")
        doc.pages[1].omitted_blank = True
        dialog = ReviewDialog(root, app, doc)
        before = doc.snapshot()
        dialog.browse(1)
        shot("01-full-file-maximized.png")
        dialog.state("normal")
        dialog.geometry("1366x700+20+20")
        shot("02-full-file-laptop.png")
        dialog.geometry("960x640+20+20")
        shot("03-full-file-small.png")
        assert doc.snapshot() == before
        doc.pending_boundaries = {3: "页码信息不明确，请检查当前文件的正文与附件。"*30}
        dialog.refresh()
        dialog.notebook.select(dialog.comparison)
        shot("04-boundary-small.png")
        dialog.notebook.select(dialog.browser)
        dialog.scope_var.set("原 PDF 全部页（含排除页）")
        dialog.reset_browser(2)
        shot("05-excluded-page.png")
        doc.pages[2].rotation_proposal = 90
        doc.pages[2].rotation_proposal_note = "程序建议旋转90°，尚未应用。"
        dialog.refresh()
        before = doc.snapshot()
        dialog.rotate(90)
        dialog.state("zoomed")
        shot("06-direction-draft.png")
        assert doc.snapshot() == before
        assert verify.file_sha256(source) == before_sha
        result = dict(captures=captures, source_unchanged=True,
                      scope="Synthetic known groups and states; navigation/preview only. No OCR or split accuracy claim.")
        (out/"visual-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True))
    finally:
        if dialog and dialog.winfo_exists():
            dialog.close()
        app.on_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="work/acceptance/review-ui-1.17.0")
    run(parser.parse_args().out)
