"""UI 冒烟测试：无 mainloop 驱动完整工作流
（导入 → 检测 → 分组 → 命名 → 撤销/重做 → 导出），并验证导出保真与源文件不变。"""

from pathlib import Path

import pytest


def _tk_available() -> bool:
    try:
        import tkinter

        tkinter.Tk().destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _tk_available(), reason="无 Tk/显示环境")


class FakeProgress:
    def __init__(self, *a, **k):
        self.canceled = False
        self.done = 0
        self.total = 0

    def set(self, done, total, phase=""):
        self.done, self.total = done, total

    def destroy(self):
        pass


@pytest.fixture
def ui_env(monkeypatch):
    from scansplit import ui

    monkeypatch.setattr(ui, "ProgressDialog", FakeProgress)
    monkeypatch.setattr(ui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(ui.messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(ui.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(ui.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr("scansplit.settings.save_settings", lambda s: None)
    return ui


def test_full_workflow(tmp_path, make_scan, ui_env, monkeypatch):
    import tkinter as tk

    import scansplit.verify as verify
    from scansplit.ui import App

    scan = make_scan("wf.pdf", ["blank", "text", "text", "blank", "text"])
    out_dir = tmp_path / "out"
    monkeypatch.setattr(ui_env.filedialog, "askdirectory", lambda **k: str(out_dir))

    src_hash = verify.file_sha256(scan)
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        # 导入
        app.import_files([str(scan)])
        assert len(app.ws.documents) == 1
        doc = app.ws.documents[0]
        assert doc.page_count == 5
        assert [(g.start, g.end) for g in doc.groups] == [(1, 5)]

        # 空白检测
        app.detect_blank()
        blanks = [p.src_index for p in doc.pages if p.blank and p.blank.verdict == "blank"]
        assert 1 in blanks and 4 in blanks

        # 自动分组
        app.auto_group_all()
        assert [(g.start, g.end) for g in doc.groups] == [(2, 3), (5, 5)]

        # 撤销 → 回到单组；重做 → 恢复两组
        app.undo()
        assert [(g.start, g.end) for g in doc.groups] == [(1, 5)]
        app.redo()
        assert [(g.start, g.end) for g in doc.groups] == [(2, 3), (5, 5)]

        # 旋转（组内整组 +90）再恢复
        app.rotate_pages_in_group(0, 90)
        assert doc.pages[1].rotation == 90 and doc.pages[2].rotation == 90
        app.undo()
        assert doc.pages[1].rotation == 0

        # OCR 命名（扫描件无文本层：有 OCR 则识别，无 OCR 则留空待确认）
        app.ocr_name()

        # 命名后的撤销快照必须可序列化（回归：dict 型候选曾致 AttributeError 崩溃）
        app.rotate_pages_in_group(0, 90)
        app.undo()
        assert doc.pages[1].rotation == 0

        # 导出（目录已 monkeypatch；空字段时 preflight 提示 → 测试中放行）
        app.export_all()
        pdfs = sorted(out_dir.glob("*.pdf"))
        assert len(pdfs) == 2

        # 导出页数与映射校验（与源页一一对应）
        from pypdf import PdfReader

        counts = sorted(len(PdfReader(str(p)).pages) for p in pdfs)
        assert counts == [1, 2]
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()
    assert verify.file_sha256(scan) == src_hash


def test_preview_zoom_fit_api(ui_env, tmp_path, make_scan):
    import tkinter as tk

    from scansplit.ui import App

    scan = make_scan("prev.pdf", ["text", "text"])
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        app.import_files([str(scan)])
        app.on_page_select(1, preview=True)
        pv = app.preview
        assert pv._doc is not None
        pv._set_zoom(2.0)
        assert pv.zoom == 2.0 and not pv.fit_mode
        pv.zoom_around(1.25)
        assert pv.zoom == 2.5
        pv.zoom_around(100)
        assert pv.zoom == pv.MAX_ZOOM  # 上限钳制
        pv.zoom_around(0.001)
        assert pv.zoom == pv.MIN_ZOOM  # 下限钳制
        pv._fit()
        assert pv.fit_mode and pv._img is not None
        pv._place()  # 居中放置不报错
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_thumb_scroll_redraws(ui_env, tmp_path, make_scan):
    """缩略图条滚动后必须重绘：滚入视口的页面不能是空白。"""
    import tkinter as tk
    from types import SimpleNamespace

    from scansplit.ui import App

    scan = make_scan("many.pdf", ["text"] * 12)
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        app.import_files([str(scan)])
        thumbs = app.thumbs
        before = {k[0] for k in thumbs._photos}
        thumbs._on_scroll("moveto", "0.9")
        after = {k[0] for k in thumbs._photos}
        assert after != before and 12 in after  # 滚到底后底部页面已绘制
        thumbs._on_wheel(SimpleNamespace(delta=-120))
        assert len(thumbs.canvas.find_all()) > 0
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_remove_doc_and_empty_state(ui_env, tmp_path, make_scan):
    import tkinter as tk

    from scansplit.ui import App

    scan = make_scan("one.pdf", ["text"])
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        app.import_files([str(scan)])
        assert len(app.ws.documents) == 1
        app.remove_doc(0)
        assert len(app.ws.documents) == 0
        assert app._active_doc() is None
        app._refresh_all()  # 空状态刷新不报错
        app.undo()
        app.redo()
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()
