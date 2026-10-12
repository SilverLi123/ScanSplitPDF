"""Window sizing must not hide side information or the export action."""
from tests.test_ui_smoke import _make_root, ui_env


def test_sidebars_expand_and_preview_does_not_dominate(ui_env,make_scan):
    from scansplit.ui import App
    root = _make_root()
    app = App(root)
    try:
        root.deiconify()
        source = make_scan("市级填报单位综合统计报告"*5+".pdf",["text"])
        app.import_files([str(source)])
        for width,height in ((1400,820),(1100,680)):
            root.geometry(f"{width}x{height}")
            root.update()
            total = app.main_panes.winfo_width()
            left_end,right_start = app.main_panes.sashpos(0),app.main_panes.sashpos(1)
            assert left_end >= total*.22
            assert right_start-left_end <= total*.35
            assert total-right_start >= 475
            assert total-right_start > right_start-left_end
            assert app.export_button.winfo_rootx()+app.export_button.winfo_width() <= root.winfo_rootx()+root.winfo_width()
            assert app.export_button.winfo_rooty()+app.export_button.winfo_height() <= root.winfo_rooty()+root.winfo_height()
            assert app.export_button.winfo_height() > 25
        assert app.selected_file_var.get() == source.name
        app.file_list.xview_moveto(1)
        root.update()
        assert app.file_list.xview()[0] > 0
    finally:
        app.on_close()


def test_fixed_export_action_shows_pending_state_and_keeps_confirmation(ui_env,make_scan):
    from scansplit.ui import App
    root = _make_root()
    app = App(root)
    try:
        assert app.export_button.cget("text") == "导出 PDF…"
        assert app.export_button.instate(["disabled"])
        app.import_files([str(make_scan("pending.pdf",["text"]))])
        group = app._active_doc().groups[0]
        assert "待检查" in app.export_notice_var.get()
        assert app.export_button.instate(["!disabled"])
        app._busy = True
        app.update_status()
        assert app.export_button.instate(["disabled"])
        app._busy = False
        app.update_status()
        assert not group.boundary_confirmed
        assert app.export_button.instate(["!disabled"])
    finally:
        app.on_close()
