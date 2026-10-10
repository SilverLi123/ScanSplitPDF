# -*- coding: utf-8 -*-
"""v1.7 智能补拆诊断：
1) 无空白页 2 页 PDF → 空白检测计数应为 0（复现用户报的计数困惑）；
2) 真实样本（11 页含空白分隔）→ smart_split_review 不误拆；
3) 合成"无空白页+页码重置"4 页扫描 → 双信号自动补拆正确。
只读真实样本；输出写临时目录。
"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from pypdf import PdfWriter

from scansplit import autoname, ui, verify
from tests import ocrimg
from tests.samplegen import _add_image_page

SAMPLE = (r"C:\Users\li333\Downloads"
          r"\%E7%BA%AF%E5%9B%BE%E7%89%87PDF_%E5%A4%9A%E6%96%87%E6%A1%A3%E7%A9%BA%E7%99%BD"
          r"%E9%A1%B5%E5%88%86%E9%9A%94_%E6%B5%8B%E8%AF%95%E6%A0%B7%E6%9C%AC.pdf")

root = tk.Tk()
root.withdraw()
app = ui.App(root)


class FakeProg:
    canceled = False

    def __init__(self, *a, **k):
        pass

    def set(self, *a, **k):
        root.update()

    def destroy(self):
        pass


with mock.patch.object(ui, "messagebox"), \
     mock.patch.object(ui, "ProgressDialog", FakeProg):

    # -- 1) 2 页无空白页 PDF：计数应为 0 ---(合成件，写临时目录)
    tmp = tempfile.mkdtemp(prefix="ssv17_")
    w = PdfWriter()
    _add_image_page(w, ocrimg.make_cn_document("测试文档一", ["正文内容。"], unit=None))
    _add_image_page(w, ocrimg.make_cn_document("", ["第二页正文。"], unit=None))
    p2 = os.path.join(tmp, "两页无空白.pdf")
    with open(p2, "wb") as f:
        w.write(f)
    app.import_files([p2])
    app.detect_blank()
    doc2 = app._active_doc()
    n_blank = sum(1 for pg in doc2.pages if pg.blank and pg.blank.verdict == "blank")
    print(f"[复现] 两页无空白 PDF：页数={doc2.page_count} 空白候选={n_blank}（应为 0）")

    # -- 2) 真实样本：智能复核不应误拆 --
    app.import_files([SAMPLE])
    rs = app._active_doc()
    app.detect_blank()
    app.auto_group_all()
    from scansplit.ocr import OcrEngine
    engine = OcrEngine.get()
    renderer = app.renderers[str(rs.path)]
    statuses = autoname.smart_split_review(rs, renderer, engine)
    print("[真实样本] smart_split_review 结果：")
    for s in statuses:
        print(f"  [{s.status}] {s.note}")
    autos = [s.next_page for s in statuses if s.status == "auto_split"]
    print(f"[真实样本] 自动补拆页：{autos}（预期 []，样本本身靠空白页分隔且无页码重置）")

    # -- 3) 合成无空白页+页码重置 → 自动补拆 --
    imgs = [
        ocrimg.make_cn_document("测试文档甲", ["正文一。"], unit=None, page_no=1),
        ocrimg.make_cn_document("", ["正文二。"], unit=None, page_no=2),
        ocrimg.make_cn_document("测试文档乙", ["正文三。"], unit=None, page_no=1),
        ocrimg.make_cn_document("", ["正文四。"], unit=None, page_no=2),
    ]
    w2 = PdfWriter()
    for im in imgs:
        _add_image_page(w2, im)
    p3 = os.path.join(tmp, "无空白页码重置.pdf")
    with open(p3, "wb") as f:
        w2.write(f)
    app.import_files([p3])
    doc3 = app._active_doc()
    app.detect_blank()
    st3 = autoname.smart_split_review(doc3, app.renderers[str(doc3.path)], engine)
    autos3 = [s.next_page for s in st3 if s.status == "auto_split"]
    print(f"[合成] 页码重置+标题双信号自动补拆页：{autos3}（预期 [3]）")

app.on_close()
root.destroy()
