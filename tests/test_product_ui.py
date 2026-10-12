"""User-visible truth, navigation and feedback; no navigation certifies evidence."""
from scansplit import presentation
from scansplit.model import Group, BlankResult, ScanDocument
from pypdf import PdfReader
from scansplit.exporter import ExportReport, ExportResult
from tests.test_ui_smoke import _make_root, ui_env, pump_until_idle


def open_pdf(path):
    return ScanDocument(path,PdfReader(path))


def test_candidate_source_matches_current_value_and_manual_text_has_no_fake_source():
    g=Group(1,2,"第二标题","单位",title_confirmed=False)
    g.title_candidates=[dict(text="第一标题",source="错误来源",page=1),dict(text="第二标题",source="标题区域",page=2)]
    status,source,severity,_=presentation.field_info(g,"title")
    assert "自动建议" in status and "原第 2 页" in source and "错误来源" not in source
    g.title_manual=True
    g.title_edit_kind="edited"
    g.title="人工标题"
    g.title_confirmed=True
    status,source,severity,match=presentation.field_info(g,"title")
    assert status=="人工修改" and source=="人工填写" and match is None


def test_automatic_acceptance_does_not_claim_manual_confirmation():
    g=Group(1,1,"标题","单位")
    assert "规则检查通过" in presentation.field_info(g,"title")[0]
    assert "人工" not in presentation.field_info(g,"title")[0]
    g.title_manual=True
    g.title_edit_kind="confirmed"
    assert presentation.field_info(g,"title")[0]=="人工确认"
    g.title_edit_kind=""
    assert "旧记录未区分" in presentation.field_info(g,"title")[0]


def test_provenance_snapshot_and_unique_migration_preserve_edit_kind(ui_env,make_scan):
    from scansplit import grouping
    d=open_pdf(make_scan("migrate.pdf",["text"]*3))
    g=Group(1,2,"人工标题","单位",title_manual=True,title_edit_kind="edited")
    d.groups=[g]
    assert Group.restore(g.snapshot()).title_edit_kind=="edited"
    grouping.refresh_groups(d)
    assert d.groups[0].title=="人工标题" and d.groups[0].title_edit_kind=="edited"
    assert not d.groups[0].boundary_confirmed


def test_step_counts_do_not_certify_unanalysed_or_stale_groups(ui_env,make_scan):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("counts.pdf",["text"]*2))])
        d=app._active_doc()
        g=d.groups[0]
        g.title,g.unit="标题","单位"
        g.need_confirm=False
        g.boundary_confirmed=True
        assert presentation.step_counts([d])[2][0]==0
        d.analysis_complete=True
        assert presentation.step_counts([d])[2][0]==1
        d.pending_boundaries={2:"uncertain"}
        assert presentation.step_counts([d])[2][0]==0
        d.pending_boundaries={}
        d.analysis_stale=True
        assert presentation.step_counts([d])[3][0]==0
        app.update_status()
        assert "自动分析" in app.step_labels[1].cget("text")
        assert "0/1" in app.step_labels[2].cget("text")
    finally:
        app.on_close()


def test_check_center_click_switches_document_and_page_without_confirmation(ui_env,make_scan):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("one.pdf",["text"])),str(make_scan("two.pdf",["text"]*3))])
        d=app.ws.documents[1]
        d.analysis_complete=True
        d.pending_boundaries={3:"请核对文件归属"}
        d.pages[1].rotation_proposal=90
        d.naming_attempted=True
        app._refresh_all()
        keys=app.check_center.items
        assert "1:boundary:3" in keys and "1:direction:2" in keys and "1:unit:0" in keys
        before=d.snapshot()
        app.check_center.tree.selection_set("1:boundary:3")
        app.check_center.selected()
        assert app._active_doc() is d and app.current_page==3
        assert d.snapshot()==before
        app.check_center.process()
        assert app._review_dialog.doc is d
        assert app._review_dialog.mode=="direction"  # direction remains the prerequisite
        app._review_dialog.close()
    finally:
        app.on_close()


def test_field_issue_focuses_exact_field_and_cards_default_to_display(ui_env,make_scan):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("fields.pdf",["text"]))])
        d=app._active_doc()
        d.analysis_complete=d.naming_attempted=True
        d.groups=[Group(1,1,"标题","",unit_confirmed=False)]
        app._refresh_all()
        panel=app.groups.panels[0]
        assert panel.title_entry.grid_info()=={}
        issue=app.check_center.items["0:unit:0"]
        app.navigate_issue(issue)
        assert app.review_tabs.select()==str(app.name_tab)
        assert panel.unit_entry.grid_info() and not panel.title_entry.grid_info()
        assert d.groups[0].unit_confirmed is False
    finally:
        app.on_close()


def test_small_window_steps_and_full_page_fit_are_visible(ui_env,make_scan):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        root.deiconify()
        app.import_files([str(make_scan("fit.pdf",["text"]))])
        for width,height in ((1400,820),(1100,680)):
            root.geometry(f"{width}x{height}")
            root.update()
            app.preview._fit()
            assert app.preview._photo.width() <= app.preview.canvas.winfo_width()
            assert app.preview._photo.height() <= app.preview.canvas.winfo_height()
            last=app.step_labels[-1]
            assert last.winfo_rootx()+last.winfo_width()<=root.winfo_rootx()+root.winfo_width()
            assert "导出" in last.cget("text")
    finally:
        app.on_close()


def test_manual_name_edit_survives_actual_reanalysis(ui_env,make_scan,monkeypatch):
    from scansplit.ui import App
    from types import SimpleNamespace
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("protect.pdf",["text"]*2))])
        d=app._active_doc()
        d.analysis_complete=d.naming_attempted=True
        d.groups[0].boundary_confirmed=True
        app._refresh_all()
        panel=app.groups.panels[0]
        panel.title_var.set("用户标题")
        panel.unit_var.set("用户填报单位")
        monkeypatch.setattr(ui_env.OcrEngine,"get",lambda:SimpleNamespace(available=False))
        app.automatic_split()
        pump_until_idle(app)
        g=d.groups[0]
        assert (g.title,g.unit)==("用户标题","用户填报单位")
        assert g.title_manual and g.unit_manual
        assert g.title_edit_kind==g.unit_edit_kind=="edited"
    finally:
        app.on_close()


def test_skip_boundary_keeps_all_decisions_and_advances(ui_env,make_scan):
    from scansplit.ui import App,ReviewDialog
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("skip.pdf",["text"]*3))])
        d=app._active_doc()
        d.analysis_complete=True
        d.pending_boundaries={2:"first",3:"second"}
        dialog=ReviewDialog(root,app,d)
        before=d.snapshot()
        assert dialog.page()==2
        dialog.skip_current()
        assert dialog.page()==3 and d.snapshot()==before
        dialog.close()
    finally:
        app.on_close()


def test_review_all_boundaries_does_not_invent_evidence(ui_env,make_scan):
    from scansplit.ui import App,ReviewDialog
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("auto.pdf",["text"]*3))])
        d=app._active_doc()
        d.analysis_complete=True
        d.groups=[Group(1,1),Group(2,3)]
        d.pages[1].split_before=True
        dialog=ReviewDialog(root,app,d)
        before=d.snapshot()
        dialog.all_boundaries()
        assert dialog.page()==2
        assert "未保存详细分析依据" in dialog.context.cget("text")
        assert d.snapshot()==before
        dialog.overview()
        dialog.close()
    finally:
        app.on_close()


def test_page_summary_keeps_original_direction_separate_from_proposal(ui_env,make_scan):
    d=open_pdf(make_scan("page.pdf",["text","blank"]))
    d.groups=[Group(1,2)]
    p=d.pages[1]
    p.original_rotation=90
    p.blank=BlankResult("blank")
    p.blank_context="internal_blank"
    p.rotation_proposal=180
    summary,direction,_=presentation.page_info(d,2)
    assert "文件 1" in summary and "内部空白" in summary and "保留" in summary
    assert "原件方向 90°" in direction and "未应用" in direction
    assert p.effective_rotation==0


def test_cancelled_export_and_empty_hash_sets_cannot_show_passed(ui_env,make_scan):
    d=open_pdf(make_scan("cancel.pdf",["text"]))
    d.groups=[Group(1,1,"标题","单位")]
    report=ExportReport(canceled=True)
    feedback=presentation.export_feedback([d],{(0,0)},report)
    assert not feedback["complete"]
    assert "源文件检查：未全部完成" in feedback["lines"]
    assert "未全部完成" in feedback["lines"][2]
    assert "通过" not in "\n".join(feedback["lines"])


def test_successful_partial_selection_uses_selected_scope(ui_env,make_scan):
    d=open_pdf(make_scan("partial.pdf",["text"]*2))
    d.groups=[Group(1,1,"甲","单位"),Group(2,2,"乙","单位")]
    report=ExportReport(results=[ExportResult(d.groups[0],d.name,ok=True)],
                        src_sha_before={str(d.path):"hash"},src_sha_after={str(d.path):"hash"})
    feedback=presentation.export_feedback([d],{(0,0)},report)
    assert feedback["complete"] and feedback["ok"]==1
    assert "所选文件" in feedback["lines"][2]


def test_export_before_and_after_state_and_invalidation(ui_env,make_scan,tmp_path,monkeypatch):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("export-state.pdf",["text"]))])
        d=app._active_doc()
        d.analysis_complete=d.naming_attempted=True
        d.groups=[Group(1,1,"标题","单位")]
        app._refresh_all()
        assert "尚未执行" in app.check_center.summary.cget("text")
        monkeypatch.setattr(ui_env.filedialog,"askdirectory",lambda **kw:str(tmp_path/"out"))
        app.export_all()
        pump_until_idle(app)
        assert app._last_export[1]["complete"]
        assert "校验通过" in app.check_center.summary.cget("text")
        app.groups.panels[0].title_var.set("新的标题")
        assert "重新检查" in app.check_center.summary.cget("text")
        assert "校验通过" not in app.check_center.summary.cget("text")
    finally:
        app.on_close()


def test_sticky_name_confirmation_tracks_edited_group_and_is_visible(ui_env,make_scan):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        root.deiconify()
        app.import_files([str(make_scan("sticky.pdf",["text"]*2))])
        d=app._active_doc()
        d.analysis_complete=d.naming_attempted=True
        d.groups=[Group(1,1,"甲","甲单位",title_confirmed=False),Group(2,2,"乙","乙单位",unit_confirmed=False)]
        app._refresh_all()
        app.review_tabs.select(app.name_tab)
        panel=app.groups.panels[1]
        panel._edit_field("unit")
        for width,height in ((1400,820),(1100,680)):
            root.geometry(f"{width}x{height}")
            root.update()
            button=app.groups.confirm_next_btn
            assert button.winfo_ismapped()
            assert button.winfo_rooty()+button.winfo_height()<=app.groups.winfo_rooty()+app.groups.winfo_height()
            assert "当前文件 2" in app.groups.current_label.cget("text")
        app.groups.confirm_current()
        assert d.groups[1].unit_manual and d.groups[1].unit_confirmed
        assert not d.groups[0].title_confirmed
        assert app.groups.current_group==0
    finally:
        app.on_close()


def test_template_edit_invalidates_export_feedback_and_refreshes_preview(ui_env,make_scan,tmp_path,monkeypatch):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("template.pdf",["text"]))])
        d=app._active_doc()
        d.analysis_complete=d.naming_attempted=True
        d.groups=[Group(1,1,"标题","单位")]
        app._refresh_all()
        monkeypatch.setattr(ui_env.filedialog,"askdirectory",lambda **kw:str(tmp_path/"out"))
        app.export_all()
        pump_until_idle(app)
        assert "校验通过" in app.check_center.summary.cget("text")
        app.template_var.set("归档-{title}——{unit}")
        assert app.groups.panels[0].fname_lbl.cget("text")=="归档-标题——单位.pdf"
        assert "重新检查" in app.check_center.summary.cget("text")
        assert "校验通过" not in app.check_center.summary.cget("text")
        def fail(*args):
            raise RuntimeError("测试导出失败")
        monkeypatch.setattr(ui_env.exporter,"export_documents",fail)
        app.export_all()
        pump_until_idle(app)
        assert app._last_export is None
        assert "operation" in app.check_center.items
        assert "测试导出失败" in app.check_center.items["operation"].reason
    finally:
        app.on_close()


def test_analysis_failure_is_visible_and_retry_keeps_pending_state(ui_env,make_scan,monkeypatch):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("task-error.pdf",["text"]))])
        d=app._active_doc()
        before=d.snapshot()
        def fail(*args):
            raise RuntimeError("测试处理失败")
        monkeypatch.setattr(ui_env.pipeline,"analyze_document",fail)
        app.automatic_split()
        pump_until_idle(app)
        assert d.snapshot()==before
        assert app.check_center.items["operation"].severity=="error"
        assert "测试处理失败" in app.check_center.items["operation"].reason
        assert not d.analysis_complete and not d.groups[0].boundary_confirmed
    finally:
        app.on_close()


def test_naming_settings_do_not_expose_or_reset_analysis_parameters(ui_env,make_scan):
    from scansplit.ui import App,SettingsDialog
    root=_make_root()
    app=App(root)
    try:
        before=vars(app.detect_params).copy()
        dialog=SettingsDialog(root,app)
        assert dialog.title()=="命名与保存位置"
        assert dialog.winfo_children()[0].winfo_manager()==""
        dialog._reset()
        dialog._ok()
        assert vars(app.detect_params)==before
    finally:
        app.on_close()
