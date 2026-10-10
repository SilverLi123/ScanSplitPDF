"""UI 冒烟测试：无 mainloop 驱动完整工作流
（导入 → 检测 → 分组 → 命名 → 撤销/重做 → 导出），并验证导出保真与源文件不变。
v1.9：后台任务系统——泵事件循环驱动 worker 完成，并覆盖取消回滚/忙碌守卫/关窗终止。"""

import time
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


def _make_root():
    """cp314 下快速创建/销毁多个 Tk 根偶发 tcl_findLibrary 抖动，重试一次兜底。"""
    import time
    import tkinter as tk

    for attempt in range(3):
        try:
            root = tk.Tk()
            root.withdraw()
            return root
        except tk.TclError:
            if attempt == 2:
                raise
            time.sleep(0.3)


class FakeProgress:
    def __init__(self, *a, **k):
        self.canceled = False
        self.done = 0
        self.total = 0

    def set(self, done, total, phase=""):
        self.done, self.total = done, total

    def winfo_exists(self):
        return True

    def destroy(self):
        pass


def pump_until_idle(app, timeout=30.0):
    """泵 Tk 事件循环直到后台任务收尾完成（v1.9 异步任务的测试驱动器）。"""
    deadline = time.time() + timeout
    while app.tasks.busy or app._busy:
        app.root.update()
        if time.time() > deadline:
            raise AssertionError("后台任务未在限时内完成")
        time.sleep(0.01)


@pytest.fixture
def ui_env(monkeypatch):
    from scansplit import ui

    monkeypatch.setattr(ui, "ProgressDialog", FakeProgress)
    monkeypatch.setattr(ui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(ui.messagebox, "showwarning", lambda *a, **k: None)
    errors = []

    def _fake_showerror(title, msg=None, **k):
        errors.append(f"{title}: {msg}")

    monkeypatch.setattr(ui.messagebox, "showerror", _fake_showerror)
    ui._showerror_log = errors  # 测试可断言"无错误弹窗"
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
    root = _make_root()
    try:
        app = App(root)
        # 导入
        app.import_files([str(scan)])
        assert len(app.ws.documents) == 1
        doc = app.ws.documents[0]
        assert doc.page_count == 5
        assert [(g.start, g.end) for g in doc.groups] == [(1, 5)]

        # 空白检测（v1.9 起为后台任务，泵事件循环至完成）
        app.detect_blank()
        pump_until_idle(app)
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
        pump_until_idle(app)

        # 命名后的撤销快照必须可序列化（回归：dict 型候选曾致 AttributeError 崩溃）
        app.rotate_pages_in_group(0, 90)
        app.undo()
        assert doc.pages[1].rotation == 0

        # 导出（目录已 monkeypatch；空字段时 preflight 提示 → 测试中放行）
        app.export_all()
        pump_until_idle(app, timeout=60)
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
    root = _make_root()
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
    root = _make_root()
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
    root = _make_root()
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


def test_step_state_and_guide(ui_env, tmp_path, make_scan):
    """v1.6：工具栏分步置灰/启用 + 空状态引导页可用。"""
    import tkinter as tk

    from scansplit.ui import App

    root = _make_root()
    try:
        app = App(root)
        # 无文档：检测/分组/命名/导出全部置灰
        app._update_step_state(0, False, 0, 0)
        for key in ("detect", "group", "ocr", "direction", "review", "export"):
            assert app.tool_btns[key].instate(["disabled"]), key
        assert "打开 PDF" in app.hint_var.get()
        # 已分组：后续步骤启用，提示导出/确认
        app._update_step_state(1, True, 2, 0)
        for key in ("detect", "group", "ocr", "direction", "review", "export"):
            assert app.tool_btns[key].instate(["!disabled"]), key
        # 有待确认字段时提示确认
        app._update_step_state(1, True, 2, 1)
        assert "待确认" in app.hint_var.get()
        # 空状态引导页绘制不报错
        app.preview.show_guide()
        # 带文档时 show() 正常回归（引导页不影响预览）
        scan = make_scan("guide.pdf", ["text"])
        app.import_files([str(scan)])
        app.on_page_select(1, preview=True)
        assert app.preview._photo is not None
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_add_split_before_keeps_pages(ui_env, tmp_path, make_scan):
    """v1.7：拆分边界记录在起点页自身——内容页绝不被排除出分组。"""
    from scansplit.ui import App

    scan = make_scan("three.pdf", ["text", "text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        app.add_split_before(2)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 1), (2, 3)]
        # 所有页面仍在组内（不丢页）
        covered = sorted(p for g in doc.groups for p in g.pages)
        assert covered == [1, 2, 3]
        # 可撤销
        app.undo()
        assert [(g.start, g.end) for g in doc.groups] == [(1, 3)]
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_field_level_confirm_in_panel(ui_env, tmp_path, make_scan):
    """v1.8 回归：面板里编辑标题只确认标题，单位的待确认状态不被整组清除。"""
    from scansplit.ui import App
    from scansplit.ui_widgets import GroupPanel

    scan = make_scan("fl.pdf", ["text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        g = doc.groups[0]
        g.title, g.unit = "旧标题", "旧单位"
        g.title_confirmed = False  # 模拟 OCR 低置信
        g.unit_confirmed = False
        panel = GroupPanel(root, app, doc, 0, g, [])
        panel.title_var.set("新标题")  # 触发 write trace
        assert g.title == "新标题"
        assert g.title_confirmed is True
        assert g.unit_confirmed is False
        assert g.need_confirm is True  # 单位仍待确认（旧行为：整组被清除）
        panel.unit_var.set("新单位")
        assert g.unit == "新单位"
        assert g.unit_confirmed is True
        assert g.need_confirm is False
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


# -- v1.9 后台任务系统专项 ---------------------------------------------------------


def _slow_detect(monkeypatch, hold):
    """把 blankdetect.detect_document 换成"阻塞到取消标志置位"的假实现，
    保证取消/关窗测试的确定性（真实检测对 5 页样本太快，无法卡在中间）。"""
    import scansplit.blankdetect as bd

    def fake(renderer, page_count, params, has_text, progress_cb, cancel_check):
        progress_cb(0, page_count)  # 与真实进度回调签名一致：(当前页, 总页数)
        deadline = time.time() + 10
        while not cancel_check() and time.time() < deadline:
            time.sleep(0.01)
        return [None] * page_count

    monkeypatch.setattr(bd, "detect_document", fake)
    return bd


def test_detect_cancel_rolls_back(tmp_path, make_scan, ui_env, monkeypatch):
    """取消检测不留半更新状态：所有页 blank 保持 None、状态不变。"""
    from scansplit.ui import App

    _slow_detect(monkeypatch, hold=None)
    scan = make_scan("cancel.pdf", ["text", "text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        app.detect_blank()
        assert app._busy  # 任务已启动（挂起在假检测里等待取消标志）
        app.tasks.cancel()
        pump_until_idle(app)
        assert all(p.blank is None for p in doc.pages)
        assert doc.status != "analyzed"
        assert not app._busy and not app.tasks.busy
        assert app.tasks.last_error is None, app.tasks.last_error
        assert not ui_env._showerror_log, ui_env._showerror_log
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_busy_guard_blocks_conflict_ops(tmp_path, make_scan, ui_env, monkeypatch):
    """任务运行期间：重复触发检测被拦（不产生第二个任务），undo/redo 被守卫。"""
    from scansplit.ui import App

    _slow_detect(monkeypatch, hold=None)
    scan = make_scan("busy.pdf", ["text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        app.detect_blank()
        thread1 = app.tasks._thread
        app.detect_blank()  # 忙碌中重复触发：应被 _busy 守卫直接 return
        assert app.tasks._thread is thread1  # 没有第二个任务
        app.undo()
        app.redo()
        app.tasks.cancel()
        pump_until_idle(app)
        assert not app.tasks.busy
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_doc_removed_discards_result(tmp_path, make_scan, ui_env):
    """文档移除后，旧任务结果按对象身份丢弃（_doc_still_open 防跨文档写入）。"""
    from scansplit.ui import App

    scan = make_scan("rm.pdf", ["text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        assert app._doc_still_open(doc)
        app.remove_doc(0)
        assert not app._doc_still_open(doc)
        other = make_scan("rm2.pdf", ["text"])
        app.import_files([str(other)])
        assert not app._doc_still_open(doc)  # 新导入的其他文档不能"顶替"旧对象
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_on_close_joins_running_task(tmp_path, make_scan, ui_env, monkeypatch):
    """关窗时后台任务在运行：先取消并 join，窗口关闭后 worker 已退出。"""
    from scansplit.ui import App

    _slow_detect(monkeypatch, hold=None)
    scan = make_scan("close.pdf", ["text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        app.detect_blank()
        assert app.tasks.busy
        app.on_close()  # 内部 cancel_and_join（超时 3 秒）并 destroy root
        assert not app.tasks.busy  # worker 已在关窗前退出
    except Exception:
        try:
            root.destroy()
        except Exception:
            pass
        raise
