"""v1.9.1 UI 交互与可靠性回归测试。

覆盖用户需求书"阶段六"要求的场景：
1. 无空白页文件完整操作流程（检测→分组→补拆）；
2. 用户拒绝自动旋转建议后方向不改变（建议与最终状态分离）；
3. 自动分组后进行智能补拆（起始页保留、可撤销）；
4. 拆分调整后的命名确认状态（人工字段保留、歧义重标待确认）；
5. 严格导出失败时的提示与阻断（字段 vs 完整性问题分开处理，fail-closed）；
6. 撤销、重做、取消后台任务。

复用 test_ui_smoke 的 Tk 测试设施（无 mainloop，泵事件循环驱动后台任务）。
"""

import time

import pytest

from tests.test_ui_smoke import (  # noqa: F401  (复用 fixture/驱动)
    FakeProgress,
    _make_root,
    pump_until_idle,
    ui_env,
)


# -- 1. 无空白页文件的完整流程 --------------------------------------------------


def test_no_blank_full_flow(ui_env, tmp_path, make_scan, monkeypatch):
    """没有空白页的连续件：检测后仍可分组/补拆，不被"尚未检测"误挡。"""
    from scansplit.ui import App

    scan = make_scan("cont.pdf", ["text", "text", "text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        assert doc.page_count == 4

        # 检测前：分组按钮置灰
        app.update_status()
        assert app.tool_btns["group"].instate(["disabled"])

        app.detect_blank()
        pump_until_idle(app)
        # 全部为内容页，无空白候选
        assert all(p.blank is not None for p in doc.pages)
        assert not app._has_blank_candidates()
        # 检测后：分组按钮**启用**（关键修复：不再把"无空白"当"未检测"）
        app.update_status()
        assert app.tool_btns["group"].instate(["!disabled"])
        assert "复核拆分" in app.hint_var.get() or "补拆" in app.hint_var.get()

        # 自动分组建立初始分组（整个文件一份）
        app.auto_group_all()
        pump_until_idle(app)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 4)]
        # 复核/补拆入口可用
        assert app.tool_btns["review"].instate(["!disabled"])
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


# -- 2. 拒绝自动旋转建议后方向不改变 --------------------------------------------


def test_reject_rotation_proposal_keeps_orientation(ui_env, tmp_path, make_scan, monkeypatch):
    """方向建议写入 rotation_proposal（不影响 effective_rotation）；
    用户拒绝后预览/导出的方向完全不变；接受后才生效。"""
    from scansplit.ui import App
    from scansplit.model import ScanDocument

    scan = make_scan("rot.pdf", ["text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()

        # 模拟 OCR 方向分析结果：第 1 页建议 90°，写为"待确认建议"
        from scansplit.autoname import DirectionResult
        results = [DirectionResult(page=1, qualities={}, suggested_angle=90,
                                   confidence=0.9, suggestion_note="检测框竖行为主")]
        # 复刻 on_done 中的建议写入逻辑
        for r in results:
            ps = doc.pages[r.page - 1]
            if r.suggested_angle not in (None, 0):
                ps.rotation_proposal = int(r.suggested_angle) % 360
        # 关键断言：建议存在，但 effective_rotation 不变（建议未生效）
        assert doc.pages[0].rotation_proposal == 90
        assert doc.pages[0].effective_rotation == 0
        assert doc.pages[0].has_rotation_proposal is True

        # 用户"拒绝"：不调用 apply_rotation，方向与建议都保持（可在对话框再选）
        app._refresh_all()
        assert doc.pages[0].effective_rotation == 0

        # 用户"接受"：写入人工方向，建议被清除，方向生效
        app.apply_rotation({1: 90}, doc=doc)
        assert doc.pages[0].effective_rotation == 90
        assert doc.pages[0].is_rotation_manual
        assert doc.pages[0].rotation_proposal is None

        # 人工方向不被后续自动检测覆盖
        doc.pages[0].detected_rotation = 270
        assert doc.pages[0].effective_rotation == 90
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_rotation_proposal_not_overwrite_manual(ui_env, tmp_path, make_scan):
    """人工已确认方向的页面：新的自动检测建议不得覆盖（且不产生待确认噪音）。"""
    from scansplit.ui import App

    scan = make_scan("rot2.pdf", ["text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        doc.pages[0].user_override_rotation = 180
        # 模拟自动检测把建议写进来时要跳过人工页（on_done 逻辑）
        ps = doc.pages[0]
        if ps.is_rotation_manual:
            ps.rotation_proposal = None
        else:
            ps.rotation_proposal = 90
        assert ps.rotation_proposal is None
        assert ps.effective_rotation == 180
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_export_rotation_equals_rendered(tmp_path, make_scan):
    """源页已有 /Rotate 时，导出结果 = 源旋转 + 额外旋转（所见即导出，不丢失）。"""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, NumberObject

    from scansplit import exporter
    from scansplit.grouping import refresh_groups
    from scansplit.model import ScanDocument
    from scansplit.loader import open_document

    scan = make_scan("rot_export2.pdf", ["text", "text"])
    # 给源文件第 1 页加 /Rotate=90
    r = PdfReader(str(scan))
    r.pages[0][NameObject("/Rotate")] = NumberObject(90)
    w = PdfWriter()
    for pg in r.pages:
        w.add_page(pg)
    src = scan.parent / "rot_src.pdf"
    with open(src, "wb") as f:
        w.write(f)

    reader, warns = open_document(src)
    doc = ScanDocument(src, reader, warns)
    assert doc.pages[0].original_rotation == 90
    # 用户额外再转 90°
    doc.pages[0].user_override_rotation = 90
    refresh_groups(doc)
    doc.groups[0].title, doc.groups[0].unit = "T", "U"
    out = tmp_path / "out"
    out.mkdir()
    opts = exporter.ExportOptions(template="{title}_{unit}", out_dir=out, strict=False)
    report = exporter.export_documents([doc], opts)
    assert len(report.succeeded) == 1
    out_pages = PdfReader(str(report.succeeded[0].path)).pages
    # 第 1 页应为 90(源) + 90(额外) = 180
    assert int(out_pages[0].get("/Rotate", 0) or 0) == 180
    # 第 2 页无旋转
    assert int(out_pages[1].get("/Rotate", 0) or 0) == 0


# -- 3. 自动分组后进行智能补拆 --------------------------------------------------


def test_smart_split_after_auto_group(ui_env, tmp_path, make_scan):
    """自动分组后新增拆分边界：起始页保留、不丢页、可撤销。"""
    from scansplit.ui import App

    scan = make_scan("split.pdf", ["text", "text", "text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        app.detect_blank()
        pump_until_idle(app)
        app.auto_group_all()
        pump_until_idle(app)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 4)]

        # 在第 3 页前新增拆分点（模拟补拆）
        app.add_split_before(3, doc=doc)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 2), (3, 4)]
        # 起始页（第 3 页）保留在组内，且全部 4 页不丢
        covered = sorted(p for g in doc.groups for p in g.pages)
        assert covered == [1, 2, 3, 4]

        # 撤销恢复
        app.undo()
        assert [(g.start, g.end) for g in doc.groups] == [(1, 4)]

        # 第 1 页前不允许新增拆分点（起始页必须保留）
        app.add_split_before(1, doc=doc)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 4)]
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_split_migration_preserves_confirmed_fields(ui_env, tmp_path, make_scan):
    """拆分边界调整后：唯一对应的组沿用人工字段；歧义组重标待确认且不误迁移标题。"""
    from scansplit.ui import App

    scan = make_scan("mig.pdf", ["text"] * 6)
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        # 先建立两段分组
        app.add_split_before(4, doc=doc)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 3), (4, 6)]
        g0, g1 = doc.groups
        g0.title, g0.unit = "标题A", "单位A"
        g0.title_confirmed = g0.unit_confirmed = True
        g1.title, g1.unit = "标题B", "单位B"
        g1.title_confirmed = g1.unit_confirmed = True

        # 切第二组（4-6 → 4-4 + 5-6）：第一组 (1,3) 仍唯一对应旧 g0 → 沿用；
        # 旧 g1(4,6) 被拆成两组 → 对应歧义 → 不得静默沿用旧标题
        app.add_split_before(5, doc=doc)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 3), (4, 4), (5, 6)]
        assert doc.groups[0].title == "标题A"  # 唯一对应 → 沿用人工字段
        assert doc.groups[0].title_confirmed is True
        # 歧义组：标题留空、标待确认（绝不把旧文件名静默迁移到错误分组）
        assert doc.groups[1].title == ""
        assert doc.groups[1].need_confirm is True
        assert doc.groups[2].title == ""
        assert doc.groups[2].need_confirm is True
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_renamed_affected_groups_preserve_manual(ui_env, tmp_path, make_scan):
    """重新识别受影响分组：人工已确认字段不被覆盖，只填补待确认字段。"""
    from scansplit.ui import App
    from scansplit import autoname

    scan = make_scan("rename.pdf", ["text", "text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        g = doc.groups[0]
        g.title, g.unit = "人工标题", "人工单位"
        g.title_confirmed = g.unit_confirmed = True
        # 直接调用底层：preserve_confirmed=True 时不得覆盖已确认字段
        autoname.auto_name_groups(doc, None, engine=None, preserve_confirmed=True)
        assert g.title == "人工标题" and g.unit == "人工单位"
        # 无待确认组时 UI 入口直接提示、不启动任务
        assert not any(gg.need_confirm for gg in doc.groups)
        app.rename_affected_groups(doc)
        assert not app._busy
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


# -- 5. 严格导出的提示与阻断 ----------------------------------------------------


def test_strict_export_blocked_on_empty_fields(ui_env, tmp_path, make_scan, monkeypatch):
    """字段缺失：严格模式提示后用户拒绝 → 不导出（0 个文件）。"""
    from scansplit.ui import App

    scan = make_scan("strict.pdf", ["text", "text"])
    out_dir = tmp_path / "out"
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        app.detect_blank()
        pump_until_idle(app)
        app.auto_group_all()
        pump_until_idle(app)
        # 标题/单位留空 → preflight 必报字段问题
        monkeypatch.setattr(ui_env.filedialog, "askdirectory", lambda **k: str(out_dir))
        monkeypatch.setattr(ui_env.messagebox, "askyesno", lambda *a, **k: False)  # 拒绝非严格
        app.export_all()
        pump_until_idle(app, timeout=30)
        assert list(out_dir.glob("*.pdf")) == []
        assert not app._busy
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_integrity_issue_not_bypassable(ui_env, tmp_path, make_scan, monkeypatch):
    """页面完整性问题（未确认依据的丢页）→ fail-closed，非严格模式也不能绕过。"""
    from scansplit.ui import App

    scan = make_scan("integ.pdf", ["text", "text", "text"])
    out_dir = tmp_path / "out"
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        # 手工构造"未确认依据的丢页"：把第 2 页设为分隔页但**不记录人工依据**
        doc.pages[1].separator = True
        from scansplit import grouping
        grouping.refresh_groups(doc)
        monkeypatch.setattr(ui_env.filedialog, "askdirectory", lambda **k: str(out_dir))
        monkeypatch.setattr(ui_env.messagebox, "askyesno", lambda *a, **k: True)  # 即便同意也不行
        app.export_all()
        pump_until_idle(app, timeout=30)
        assert list(out_dir.glob("*.pdf")) == []
        # 应弹出错误（被阻止）
        assert any("完整性" in e or "阻止" in e for e in ui_env._showerror_log), ui_env._showerror_log
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_manual_separator_recorded_and_snapshotted(ui_env, tmp_path, make_scan):
    """人工标记分隔页要记录到 manual_separator_pages，并随撤销快照回滚。"""
    from scansplit.ui import App

    scan = make_scan("mansep.pdf", ["text", "text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        app.set_separator([2], True)
        assert 2 in doc.manual_separator_pages
        assert doc.pages[1].separator is True
        # 撤销 → 依据一并回滚
        app.undo()
        assert 2 not in doc.manual_separator_pages
        assert doc.pages[1].separator is False
        app.redo()
        assert 2 in doc.manual_separator_pages
        # 取消分隔标记 → 依据移除
        app.set_separator([2], False)
        assert 2 not in doc.manual_separator_pages
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


# -- 6. 撤销 / 重做 / 取消后台任务 ----------------------------------------------


def test_undo_redo_cancel_flow(ui_env, tmp_path, make_scan, monkeypatch):
    """撤销/重做/取消后台任务组合场景。"""
    from scansplit.ui import App
    import scansplit.blankdetect as bd

    def fake_detect(renderer, page_count, params, has_text, progress_cb, cancel_check):
        progress_cb(0, page_count)
        deadline = time.time() + 10
        while not cancel_check() and time.time() < deadline:
            time.sleep(0.01)
        return [None] * page_count

    monkeypatch.setattr(bd, "detect_document", fake_detect)
    scan = make_scan("undo.pdf", ["text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        # 启动后台任务并取消 → 回滚，无半更新
        app.detect_blank()
        assert app._busy
        app.tasks.cancel()
        pump_until_idle(app)
        assert all(p.blank is None for p in doc.pages)
        assert not app._busy and not app.tasks.busy
        assert not ui_env._showerror_log

        # 撤销/重做在空栈时安全
        app.undo()
        app.redo()
        # 一次真实编辑后的撤销/重做
        app.add_split_before(2, doc=doc)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 1), (2, 2)]
        app.undo()
        assert [(g.start, g.end) for g in doc.groups] == [(1, 2)]
        app.redo()
        assert [(g.start, g.end) for g in doc.groups] == [(1, 1), (2, 2)]
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()


def test_export_name_conflicts_detected(ui_env, tmp_path, make_scan):
    """同名标题/单位的分组应被识别为重名冲突。"""
    from scansplit.ui import App

    scan = make_scan("dup.pdf", ["text", "text", "text", "text"])
    root = _make_root()
    try:
        app = App(root)
        app.import_files([str(scan)])
        doc = app._active_doc()
        app.add_split_before(3, doc=doc)
        g0, g1 = doc.groups
        g0.title, g0.unit = "同名", "同单位"
        g1.title, g1.unit = "同名", "同单位"
        conflicts = app.export_name_conflicts()
        assert any(c > 1 for c in conflicts.values()), conflicts
    finally:
        try:
            app.on_close()
        except Exception:
            root.destroy()
