# -*- coding: utf-8 -*-
"""v1.6 体验优化的真实样本诊断脚本（只读真实样本，输出写临时目录）。"""
import os
import sys
import glob
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

from scansplit import ui, verify

SAMPLE = (r"C:\Users\li333\Downloads"
          r"\%E7%BA%AF%E5%9B%BE%E7%89%87PDF_%E5%A4%9A%E6%96%87%E6%A1%A3%E7%A9%BA%E7%99%BD"
          r"%E9%A1%B5%E5%88%86%E9%9A%94_%E6%B5%8B%E8%AF%95%E6%A0%B7%E6%9C%AC.pdf")

root = tk.Tk()
root.withdraw()
app = ui.App(root)
src_hash = verify.file_sha256(SAMPLE)
out_dir = tempfile.mkdtemp(prefix="ssv16_")


class FakeProg:
    canceled = False

    def __init__(self, *a, **k):
        pass

    def set(self, *a, **k):
        root.update()

    def destroy(self):
        pass


print("== 空状态引导（show_guide）已随启动刷新执行，无异常即通过 ==")
with mock.patch.object(ui, "messagebox"), \
     mock.patch.object(ui, "ProgressDialog", FakeProg), \
     mock.patch.object(ui.filedialog, "askdirectory", return_value=out_dir):
    app.import_files([SAMPLE])
    doc = app._active_doc()
    print(f"导入：{doc.name} {doc.page_count} 页")
    app.detect_blank()
    app.auto_group_all()
    print("分组：", [(g.start, g.end) for g in doc.groups])
    app.ocr_name()
    for gi, g in enumerate(doc.groups):
        print(f"  组{gi + 1} [{g.start}-{g.end}] title={g.title!r} unit={g.unit!r} need_confirm={g.need_confirm}")
    # 新功能 B：组级方向分析（组 2 = 侧放表格，应建议 270°）
    g2 = doc.groups[1]
    app.direction_analyze_group(1, doc)
    print(f"组2 整组旋转后 rotation：{[doc.pages[p - 1].rotation for p in g2.pages]}")
    app.export_all()
    print("导出文件：", sorted(os.path.basename(p) for p in glob.glob(os.path.join(out_dir, "*.pdf"))))

same = verify.file_sha256(SAMPLE) == src_hash
print("源文件 SHA-256 前后一致：", same)
app.on_close()
root.destroy()
sys.exit(0 if same else 1)
