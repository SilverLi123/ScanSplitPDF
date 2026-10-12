"""Human review remains necessary; navigation must not certify evidence."""
from types import SimpleNamespace
from scansplit.model import Group
from scansplit.workflow import next_step
from tests.test_ui_smoke import _make_root, pump_until_idle, ui_env


def doc(groups=None, **kwargs):
    values = dict(name="样本.pdf", groups=groups or [Group(1, 2)], pages=[],
                  analysis_complete=True, analysis_stale=False, naming_attempted=False,
                  pending_boundaries={})
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_empty_fields_are_not_export_ready_despite_legacy_confirmed_defaults():
    d = doc()
    assert next_step([d]).action == "name"
    d.naming_attempted = True
    assert next_step([d]).action == "names"


def test_all_documents_split_before_any_naming_and_no_navigation_side_effects():
    ready = doc([Group(1, 2, "A", "B")], naming_attempted=True)
    unresolved = doc(pending_boundaries={2: "uncertain"})
    snapshot = unresolved.pending_boundaries.copy()
    result = next_step([ready, unresolved])
    assert result.action == "review" and result.doc_index == 1
    assert unresolved.pending_boundaries == snapshot
    unresolved.pending_boundaries.clear()
    unresolved.pages = [SimpleNamespace(has_rotation_proposal=True)]
    assert next_step([ready, unresolved]).action == "review"
    unresolved.pages = []
    unresolved.analysis_stale = True
    assert next_step([ready, unresolved]).action == "reanalyze"
    unresolved.analysis_stale = False
    unresolved.groups = [Group(1, 2, "C", "D")]
    assert next_step([ready, unresolved]).action == "export"


def test_preview_rotation_does_not_apply_until_confirm_and_then_reanalyzes(ui_env, make_scan, monkeypatch):
    from scansplit.ui import App, ReviewDialog
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("turn.pdf", ["text", "text"]))])
        d = app._active_doc()
        d.analysis_complete = True
        d.pages[0].rotation_proposal = 90
        d.manual_boundaries[2] = False
        d.groups[0].title, d.groups[0].title_manual = "人工标题", True
        calls = []
        def analyze(target, *args):
            calls.append(target)
            assert target.pages[0].is_rotation_manual
            assert target.pages[0].effective_rotation == 90
            target.analysis_stale = False
            target.pending_boundaries = {2: "人工检查"}
        monkeypatch.setattr(ui_env.pipeline, "analyze_document", analyze)
        dialog = ReviewDialog(root, app, d)
        dialog.rotate(90)
        assert d.pages[0].effective_rotation == 0
        assert d.pages[0].has_rotation_proposal
        dialog.confirm_direction()
        pump_until_idle(app)
        dialog.refresh()
        assert calls == [d]
        assert dialog.mode == "boundary"
        assert d.manual_boundaries[2] is False
        assert d.groups[0].title == "人工标题"
        dialog.close()
    finally:
        app.on_close()


def test_failed_reanalysis_keeps_confirmed_direction_and_pending_work(ui_env, make_scan, monkeypatch):
    from scansplit.ui import App, ReviewDialog
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("fail.pdf", ["text"]))])
        d = app._active_doc()
        d.analysis_complete = True
        d.pages[0].rotation_proposal = 90
        def fail(*args):
            raise RuntimeError("test failure")
        monkeypatch.setattr(ui_env.pipeline, "analyze_document", fail)
        dialog = ReviewDialog(root, app, d)
        dialog.rotate(90)
        dialog.confirm_direction()
        pump_until_idle(app)
        dialog.refresh()
        assert d.pages[0].effective_rotation == 90
        assert d.analysis_stale and not d.groups[0].boundary_confirmed
        assert next_step([d]).action == "reanalyze"
        assert dialog.mode == "update"
        dialog.close()
    finally:
        app.on_close()


def test_boundary_answer_preserves_pages_and_close_preserves_unanswered(ui_env, make_scan):
    from scansplit.ui import App, ReviewDialog
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("split.pdf", ["text"]*4))])
        d = app._active_doc()
        d.analysis_complete = True
        d.pending_boundaries = {2:"maybe", 3:"maybe"}
        dialog = ReviewDialog(root, app, d)
        dialog.resolve(True)
        assert [(g.start,g.end) for g in d.groups] == [(1,1),(2,4)]
        assert not d.separators
        assert dialog.page() == 3
        dialog.close()
        assert d.pending_boundaries == {3:"maybe"}
        assert next_step([d]).action == "review"
    finally:
        app.on_close()


def test_overview_manual_split_merge_and_undo_preserve_page_coverage(ui_env, make_scan):
    from scansplit.ui import App, ReviewDialog
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("overview.pdf", ["text"]*4))])
        d = app._active_doc()
        d.analysis_complete = True
        d.groups[0].boundary_confirmed = True
        dialog = ReviewDialog(root, app, d)
        assert dialog.mode == "groups"
        dialog.split_page_var.set("3")
        dialog.manual_split()
        assert [(g.start,g.end) for g in d.groups] == [(1,2),(3,4)]
        dialog.tree.selection_set("g:1")
        dialog.merge_previous()
        assert [(g.start,g.end) for g in d.groups] == [(1,4)]
        dialog.undo()
        assert [(g.start,g.end) for g in d.groups] == [(1,2),(3,4)]
        assert not d.separators
        dialog.close()
    finally:
        app.on_close()


def test_name_confirmation_does_not_certify_other_groups_or_empty_values(ui_env, make_scan):
    from scansplit.ui import App
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("names.pdf", ["text"]*4))])
        d = app._active_doc()
        d.analysis_complete = d.naming_attempted = True
        d.groups = [Group(1,2,"甲","甲单位",title_confirmed=False,unit_confirmed=False),
                    Group(3,4,"乙","乙单位",title_confirmed=False,unit_confirmed=False)]
        app._refresh_all()
        first = app.groups.panels[0]
        first._confirm_name()
        assert not d.groups[0].need_confirm
        assert d.groups[1].need_confirm
        assert app.current_page == 3
        first.title_var.set("")
        first._confirm_name()
        assert not d.groups[0].title_confirmed
        assert next_step([d]).action == "names"
    finally:
        app.on_close()


def test_export_checks_list_before_prompting_for_folder(ui_env, make_scan, monkeypatch):
    from scansplit.ui import App
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("blocked.pdf", ["text"]))])
        monkeypatch.setattr(ui_env.filedialog, "askdirectory", lambda **kw: (_ for _ in ()).throw(AssertionError("must not ask on blocked export")))
        app.export_all()
    finally:
        app.on_close()


def test_workflow_snapshot_restores_reanalysis_and_naming_stage(ui_env, make_scan):
    from scansplit.ui import App
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("snapshot.pdf", ["text"]))])
        d = app._active_doc()
        d.analysis_complete = d.naming_attempted = True
        before = d.snapshot()
        app.apply_rotation({1:90}, d)
        assert d.analysis_stale
        d.restore(before)
        assert not d.analysis_stale and d.naming_attempted
        assert d.pages[0].effective_rotation == 0
    finally:
        app.on_close()


def test_small_review_keeps_decision_buttons_visible(ui_env, make_scan):
    from scansplit.ui import App, ReviewDialog
    root = _make_root()
    app = App(root)
    try:
        root.geometry("1100x680")
        root.deiconify()
        app.import_files([str(make_scan("small.pdf", ["text"]*4))])
        d = app._active_doc()
        d.analysis_complete = True
        d.pending_boundaries = {3:"test"}
        dialog = ReviewDialog(root, app, d)
        dialog.geometry("960x640")
        root.update()
        button = dialog.split_button
        assert button.winfo_y()+button.winfo_height() <= dialog.actions.winfo_height()
        assert dialog.actions.winfo_y()+dialog.actions.winfo_height() < dialog.winfo_height()
        assert abs(dialog.left_page.winfo_width()-dialog.right_page.winfo_width()) <= 2
        dialog.right_page.draw()
        assert dialog.right_page.photo.height() <= dialog.right_page.canvas.winfo_height()
        dialog.close()
    finally:
        app.on_close()


def test_guided_primary_action_through_real_ocr_and_lossless_export(ui_env, tmp_path, monkeypatch):
    from scansplit.ui import App
    from scansplit import verify
    from pypdf import PdfReader
    from tests.ocrimg import make_cn_document
    source = tmp_path/"真实OCR流程.pdf"
    make_cn_document("教学设备统计表", body_lines=["填报单位：教务处", "项目名称 数量 负责人 备注"], page_no=1).save(source,"PDF",resolution=144)
    source_hash = verify.file_sha256(source)
    out = tmp_path/"export"
    monkeypatch.setattr(ui_env.filedialog,"askdirectory",lambda **kw:str(out))
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(source)])
        app.workflow_next()
        pump_until_idle(app)
        d = app._active_doc()
        dialog = app._review_dialog
        # This clear upright one-document source now reaches name cards directly.
        # No simulated human direction/boundary decisions may hide an auto failure.
        assert dialog is None and d.analysis_complete and d.naming_attempted
        assert d.groups[0].boundary_confirmed and not d.pending_boundaries
        assert not d.manual_boundaries and all(not p.is_rotation_manual for p in d.pages)
        assert all(not p.has_rotation_proposal and p.effective_rotation==0 for p in d.pages)
        assert d.groups[0].title == "教学设备统计表"
        assert d.groups[0].unit == "教务处"
        app.groups.panels[0]._confirm_name()
        assert next_step([d]).action == "export"
        app.workflow_next()
        pump_until_idle(app)
        files = list(out.glob("*.pdf"))
        assert len(files) == 1
        assert len(PdfReader(str(files[0])).pages) == 1
        assert verify.file_sha256(source) == source_hash
        assert list(out.glob("ScanSplit-导出记录-*.json"))
        assert not ui_env._showerror_log
    finally:
        app.on_close()
