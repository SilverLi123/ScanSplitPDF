"""Capture the app's own synthetic UI states for bounded visual QA (no user data)."""
from pathlib import Path
import argparse
import time
import tkinter as tk
from PIL import ImageGrab
from scansplit.main import _enable_dpi_awareness
from scansplit import settings
from scansplit.ui import App, ReviewDialog
from scansplit.model import Group, BlankResult


def run(out):
    _enable_dpi_awareness()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    settings.load_settings = lambda: {}
    settings.save_settings = lambda _: None
    root = tk.Tk()
    root.geometry("1400x820+20+20")
    app = App(root)
    root.attributes("-topmost", True)
    def shot(window, name):
        window.lift()
        window.update()
        time.sleep(.3)
        window.update()
        x, y = window.winfo_rootx(), window.winfo_rooty()
        ImageGrab.grab((x,y,x+window.winfo_width(),y+window.winfo_height())).save(out/name)
    try:
        shot(root,"01-empty.png")
        app.import_files([str(Path("work/acceptance/final/混合扫描验收.pdf").resolve())])
        d = app._active_doc()
        d.analysis_complete = True
        for p in d.pages:
            p.blank = BlankResult("blank" if p.src_index in (3,5,9) else "content", 1)
        d.pending_boundaries = {6:"出现新的填报单位，需核对文件归属",8:"标题改变，缺少可靠的页码证据"}
        d.pages[5].rotation_proposal=90
        d.pages[5].rotation_proposal_note="建议旋转 90°，需人工检查"
        app._refresh_all()
        shot(root,"02-main-review.png")
        app.check_center.tree.selection_set("0:boundary:6")
        app.check_center.selected()
        shot(root,"02b-check-center.png")
        dialog=ReviewDialog(root,app,d)
        shot(dialog,"03-direction.png")
        d.pages[5].rotation_proposal=None
        d.pages[5].rotation=90
        dialog.refresh()
        shot(dialog,"04-boundary.png")
        d.pending_boundaries={}
        for p in (5,9):
            d.pages[p-1].separator = True
            d.manual_separator_pages.add(p)
        d.groups=[Group(1,4,"教学设备统计表","教务处"),Group(6,7,"教学设备统计表","科研处"),
                  Group(8,8,"资产盘点汇总表","总务处"),Group(10,10,"工作情况说明",""),Group(11,11,"人员登记表","")]
        dialog.refresh()
        shot(dialog,"05-overview.png")
        dialog.geometry("960x640+30+30")
        shot(dialog,"06-review-small.png")
        dialog.close()
        d.naming_attempted=True
        d.groups[0].title_confirmed=False
        d.groups[0].title_candidates=[dict(text="教学设备统计表",page=1,source="首页标题区域 OCR",confidence=.7)]
        d.groups[0].unit_candidates=[dict(text="教务处",page=1,source="填报单位标签 OCR",confidence=.95)]
        d.groups[1].unit=""
        app._refresh_all()
        app.focus_next_name()
        shot(root,"07-names.png")
        root.geometry("1100x680+20+20")
        shot(root,"08-main-small.png")
        app.review_tabs.select(app.check_center)
        shot(root,"09-check-small.png")
        print(str(out.resolve()))
    finally:
        app.on_close()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("out")
    run(parser.parse_args().out)
