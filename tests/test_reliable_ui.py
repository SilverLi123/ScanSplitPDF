import threading
from scansplit.model import Group
from tests.test_ui_smoke import _make_root, pump_until_idle, ui_env


def test_boundary_review_preview_and_keep(ui_env, make_scan):
    from scansplit.ui import App, ReviewDialog
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("review.pdf", ["text"]*3))])
        doc = app._active_doc()
        doc.analysis_complete = True
        doc.pending_boundaries = {3:"归属不明"}
        dialog = ReviewDialog(root, app, doc)
        dialog.preview(0)
        assert app.current_page == 3
        dialog.resolve(False)
        assert doc.manual_boundaries[3] is False
        assert not doc.pending_boundaries
        assert not doc.pages[2].split_before
        dialog.destroy()
    finally:
        app.on_close()


def test_manual_whole_group_confirmation_protects_internal_boundaries(ui_env, make_scan):
    from scansplit.ui import App
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("whole.pdf", ["text","blank","text"]))])
        doc = app._active_doc()
        app.confirm_group_boundary(doc,0)
        assert doc.groups[0].boundary_confirmed
        assert doc.manual_boundaries == {1:False,2:False,3:False}
        assert doc.manual_separator_pages == {1,2,3}
    finally:
        app.on_close()


def test_one_manual_split_does_not_certify_unanalysed_groups(ui_env, make_scan):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files([str(make_scan("unchecked.pdf",["text"]*4))])
        app.add_split_before(3)
        assert all(not g.boundary_confirmed for g in app._active_doc().groups)
        app.confirm_group_boundary(app._active_doc(),0)
        assert app._active_doc().groups[0].boundary_confirmed
        assert not app._active_doc().groups[1].boundary_confirmed
    finally:
        app.on_close()


def test_all_manual_rotation_paths_invalidate_automatic_evidence(ui_env, make_scan):
    from scansplit.ui import App
    root = _make_root()
    app = App(root)
    try:
        app.import_files([str(make_scan("stale.pdf", ["text","text"]))])
        doc=app._active_doc()
        doc.analysis_complete=True
        g=doc.groups[0]
        g.boundary_confirmed=g.title_confirmed=g.unit_confirmed=True
        g.title="自动标题"
        g.unit="人工单位"
        g.unit_manual=True
        doc.pages[0].rotation_proposal=90
        app.sel_pages={1}
        app.rotate_selection(90)
        assert doc.pages[0].rotation_proposal is None
        assert not g.boundary_confirmed and not g.title_confirmed
        assert g.unit == "人工单位" and g.unit_confirmed
        assert 1 in doc.pending_boundaries
        app.undo()
        assert doc.pages[0].rotation == 0
        assert doc.pages[0].rotation_proposal == 90
    finally:
        app.on_close()


def test_async_import_is_atomic_and_delivers_on_main_thread(ui_env, make_scan):
    from scansplit.ui import App
    root=_make_root()
    app=App(root)
    try:
        app.import_files_async([str(make_scan("async.pdf",["text"]*4))])
        pump_until_idle(app)
        assert len(app.ws.documents)==1
        assert app._active_doc().page_count==4
        assert not app._active_doc().groups[0].boundary_confirmed
    finally:
        app.on_close()


def test_cancel_returns_partial_report_to_ui():
    from scansplit.tasks import TaskManager
    class Root:
        pending=[]
        def after(self, delay, fn):
            self.pending.append(fn)
    root=Root()
    manager=TaskManager(root,lambda msg:None)
    started, release=threading.Event(), threading.Event()
    result={"verified_files":["already-written.pdf"]}
    done=[]
    def work(progress,log,cancel):
        started.set()
        release.wait(2)
        return result
    manager.start("export",work,lambda report,canceled,error:done.append((report,canceled,error)))
    assert started.wait(1)
    manager.cancel()
    release.set()
    manager._thread.join(2)
    while root.pending and not done:
        root.pending.pop(0)()
    assert done == [(result,True,None)]
