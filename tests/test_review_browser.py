"""Readable review windows, full-file navigation, and explicit decision targets."""
import re
import weakref
from types import SimpleNamespace

import pytest

from scansplit import review_ui
from scansplit.model import Group, BlankResult
from tests.test_ui_smoke import ui_env, _make_root


def open_review(ui_env,make_scan,pages=6):
    root=_make_root()
    app=ui_env.App(root)
    app.settings.pop("review_window_geometry",None)
    app.settings.pop("review_window_maximized",None)
    app.import_files([str(make_scan("review.pdf",["text"]*pages))])
    d=app._active_doc()
    d.analysis_complete=True
    dialog=review_ui.ReviewDialog(root,app,d)
    return root,app,d,dialog


@pytest.mark.parametrize("saved",["4000x3000+9999-9999","invalid","", "640x480+-780+30"])
def test_restored_geometry_stays_on_parent_monitor_and_small_desktop(monkeypatch,saved):
    monkeypatch.setattr(review_ui,"work_area",lambda win:(-800,0,0,600))
    class Window:
        def resizable(self,*args): pass
        def minsize(self,w,h): self.minimum=(w,h)
        def geometry(self,value): self.value=value
        def state(self,value): self.window_state=value
    win=Window()
    review_ui.configure_window(win,SimpleNamespace(settings=dict(review_window_geometry=saved)))
    values=re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)",win.value).groups()
    w,h,x,y=[int(v[1:] if v.startswith("+") else v) for v in values]
    assert "+-" in win.value  # Tk's absolute negative coordinate, not right-edge-relative.
    assert -800<=x and x+w<=0 and 0<=y and y+h<=600
    assert win.minimum[0]<=800 and win.minimum[1]<=600 and win.window_state=="zoomed"


def test_default_maximized_full_file_browsing_never_confirms_pages(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        before=d.snapshot()
        assert dialog.state()=="zoomed" and str(dialog.notebook.select())==str(dialog.browser)
        assert dialog.browser_pages==[1,2,3,4,5,6] and dialog.browser_page.fit_mode=="page"
        dialog.browse(1)
        assert dialog.browser_number==2 and dialog.page()==1
        dialog.browse_at(3)
        assert dialog.browser_number==4 and "第 4 / 6 页" in dialog.position.cget("text")
        dialog.browse_at(5)
        assert dialog.next_button.instate(["disabled"]) and dialog.last_button.instate(["disabled"])
        dialog.browse_at(0)
        assert dialog.previous_button.instate(["disabled"])
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_retained_page_numbers_and_original_pdf_scope_keep_exclusions_visible(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        d.groups=[Group(1,4,excluded_pages=(2,)),Group(5,6)]
        d.pages[1].omitted_blank=True
        d.pages[1].blank=BlankResult("blank")
        d.manual_separator_pages.add(2)
        dialog.refresh()
        before=d.snapshot()
        assert dialog.browser_pages==[1,3,4]
        dialog.browse(1)
        assert dialog.browser_number==3 and "第 2 / 3 页" in dialog.position.cget("text")
        dialog.page_var.set("2")
        dialog.jump()
        assert dialog.browser_number==3 and "不在当前" in dialog.browse_hint.cget("text")
        dialog.scope_var.set("原 PDF 全部页（含排除页）")
        dialog.reset_browser(2)
        assert dialog.browser_number==2 and "删除" in dialog.browse_hint.cget("text")
        assert dialog.browser_pages==list(range(1,7)) and d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_boundary_confirmation_targets_original_selection_after_browsing_other_pages(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        d.pending_boundaries={3:"交界3",5:"交界5"}
        dialog.refresh()
        assert dialog.page()==3 and dialog.browser_number==3
        before=d.snapshot()
        dialog.browse_at(5)
        assert dialog.browser_number==6 and dialog.page()==3
        assert "原第 3 页" in dialog.question.cget("text") and d.snapshot()==before
        dialog.resolve(True)
        assert d.manual_boundaries=={3:True} and d.pending_boundaries=={5:"交界5"}
        assert d.pages[2].split_before and not d.pages[5].split_before
    finally:
        dialog.close()
        app.on_close()


def test_split_from_viewed_page_and_undo_preserve_all_pages(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        original=d.path.read_bytes()
        dialog.browse_at(2)
        dialog.split_viewed()
        assert [(g.start,g.end) for g in d.groups]==[(1,2),(3,6)]
        assert [p for g in d.groups for p in g.pages]==list(range(1,7))
        dialog.undo()
        assert [(g.start,g.end) for g in d.groups]==[(1,6)] and not d.manual_boundaries
        assert d.path.read_bytes()==original
    finally:
        dialog.close()
        app.on_close()


@pytest.mark.parametrize("number",[1,2,5,0,99])
def test_manual_split_rejects_first_page_exclusions_and_other_groups(ui_env,make_scan,number):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        d.groups=[Group(1,4,excluded_pages=(2,)),Group(5,6)]
        d.pages[1].omitted_blank=True
        dialog.refresh()
        before=d.snapshot()
        dialog.split_page_var.set(str(number))
        dialog.manual_split()
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_zoom_keyboard_and_jump_preserve_review_target_and_manual_state(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        before=d.snapshot()
        dialog.browser_page.scale(1.25)
        dialog.browse(1)
        assert dialog.browser_page.zoom==1.25 and dialog.browser_page.fit_mode=="page"
        dialog.browser_page.wheel(SimpleNamespace(state=4,delta=120))
        assert dialog.browser_page.zoom==1.5625
        dialog.key_browse(SimpleNamespace(widget=dialog.page_entry),1)
        assert dialog.browser_number==2
        dialog.key_browse(SimpleNamespace(widget=dialog.browser_page.canvas),1)
        assert dialog.browser_number==3
        dialog.page_var.set("5")
        dialog.jump()
        assert dialog.browser_number==5 and dialog.page()==1
        dialog.page_var.set("invalid")
        dialog.jump()
        assert dialog.browser_number==5 and "有效" in dialog.browse_hint.cget("text")
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_direction_draft_has_large_single_page_preview_and_never_applies_on_navigation(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        d.pages[1].rotation_proposal=90
        dialog.refresh()
        before=d.snapshot()
        assert dialog.mode=="direction" and dialog.browser_pages==[2] and dialog.scope.instate(["disabled"])
        dialog.rotate(90)
        assert dialog.browser_page.angle==90 and dialog.right_page.angle==90
        assert d.pages[1].effective_rotation==0 and d.snapshot()==before
        dialog.suggest()
        assert dialog.browser_page.angle==90 and d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_window_size_is_remembered_and_sidebar_can_be_hidden(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    reopened=None
    try:
        root.deiconify()
        root.update()
        dialog.state("normal")
        dialog.geometry("1100x700+40+40")
        root.update()
        dialog.toggle_queue()
        assert str(dialog.queue) not in dialog.body.panes()
        dialog.toggle_queue()
        assert str(dialog.queue) in dialog.body.panes()
        dialog.close()
        assert app.settings["review_window_geometry"].startswith("1100x700")
        assert app.settings["review_window_maximized"] is False
        reopened=review_ui.ReviewDialog(root,app,d)
        root.update()
        assert reopened.state()=="normal" and reopened.winfo_width()==1100 and reopened.winfo_height()==700
    finally:
        if reopened: reopened.close()
        if dialog.winfo_exists(): dialog.close()
        app.on_close()


def test_small_window_with_long_reason_keeps_actions_visible(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        root.deiconify()
        d.pending_boundaries={3:"较长的判断依据，需要核对正文和附件。"*100}
        dialog.refresh()
        root.update()
        dialog.state("normal")
        dialog.geometry("960x640+20+20")
        root.update()
        assert dialog.actions.winfo_y()+dialog.actions.winfo_height()<dialog.winfo_height()
        assert dialog.split_button.winfo_ismapped()
        assert dialog.left_page.canvas.winfo_height()>60
        assert dialog.right_page.canvas.winfo_height()>60
        assert len(dialog.context.cget("text"))>1000
        assert dialog.context.master.yview()[1]<1
    finally:
        dialog.close()
        app.on_close()


def test_direction_issue_opens_unified_visual_review(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        dialog.close()
        d.pages[2].rotation_proposal=180
        app.show_direction_proposals(d)
        dialog=app._review_dialog
        assert isinstance(dialog,review_ui.ReviewDialog) and dialog.mode=="direction"
        assert dialog.browser_number==3 and d.pages[2].effective_rotation==0
    finally:
        dialog.close()
        app.on_close()


def test_high_scaling_small_window_keeps_navigation_and_preview_usable(ui_env,make_scan):
    root=_make_root()
    root.tk.call("tk","scaling",2.0)
    app=ui_env.App(root)
    dialog=None
    try:
        app.settings.pop("review_window_geometry",None)
        app.settings.pop("review_window_maximized",None)
        app.import_files([str(make_scan("scaled.pdf",["text"]*4))])
        d=app._active_doc()
        d.analysis_complete=True
        root.deiconify()
        root.update()
        dialog=review_ui.ReviewDialog(root,app,d)
        root.update()
        assert dialog.state()=="zoomed"
        dialog.state("normal")
        dialog.geometry("960x640+20+20")
        root.update()
        assert dialog.winfo_width()==960 and dialog.winfo_height()==640
        assert dialog.browser_page.canvas.winfo_height()>=150
        for button in (dialog.first_button,dialog.previous_button,dialog.next_button,
                       dialog.last_button,dialog.jump_button):
            assert button.winfo_ismapped()
            assert button.winfo_x()+button.winfo_width()<=button.master.winfo_width()
        for button in dialog.actions.winfo_children():
            assert button.winfo_x()+button.winfo_width()<=dialog.actions.winfo_width()
    finally:
        if dialog: dialog.close()
        app.on_close()


def test_closed_dialog_releases_its_tcl_variables_on_ui_thread(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        before=d.snapshot()
        variables=[weakref.ref(getattr(dialog,name)) for name in ("scope_var","page_var","split_page_var")]
        dialog.close()
        assert all(reference() is None for reference in variables)
        assert d.snapshot()==before
    finally:
        if dialog.winfo_exists(): dialog.close()
        app.on_close()


def test_first_retained_page_cannot_create_an_empty_document(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        d.pages[0].omitted_blank=True
        d.groups=[Group(1,6,excluded_pages=(1,))]
        dialog.refresh()
        before=d.snapshot()
        assert dialog.browser_number==2 and dialog.view_split_button.instate(["disabled"])
        dialog.split_page_var.set("2")
        dialog.manual_split()
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def assert_complete_page(preview):
    preview.draw()
    assert preview.photo is not None
    assert preview.photo.width()<=preview.canvas.winfo_width()
    assert preview.photo.height()<=preview.canvas.winfo_height()
    box=preview.canvas.bbox("all")
    assert box[0]>=0 and box[1]>=0
    assert box[2]<=preview.canvas.winfo_width() and box[3]<=preview.canvas.winfo_height()


def test_whole_page_remains_visible_after_navigation_resize_and_rotation(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        root.deiconify()
        root.update()
        before=d.snapshot()
        dialog.state("normal")
        for geometry in ("1366x768+20+20","960x640+20+20"):
            dialog.geometry(geometry)
            root.update()
            for page,angle in ((1,0),(2,90),(3,0)):
                dialog.browse_at(page-1)
                dialog.browser_page.show(page,"整页验证",angle,reset_zoom=False)
                root.update()
                assert dialog.browser_page.fit_mode=="page" and dialog.browser_page.zoom==1
                assert_complete_page(dialog.browser_page)
        # Explicit enlargement remains available; returning to whole-page fits again.
        dialog.browser_page.fit_width()
        assert dialog.browser_page.fit_mode=="width"
        dialog.browse(1)
        assert dialog.browser_page.fit_mode=="width"
        dialog.browser_page.scale(0)
        assert_complete_page(dialog.browser_page)
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_boundary_opens_two_complete_pages_and_restores_pair_for_next_target(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan)
    try:
        root.deiconify()
        root.update()
        d.pending_boundaries={3:"第一处交界",5:"第二处交界"}
        dialog.refresh()
        dialog.state("normal")
        dialog.geometry("960x640+20+20")
        root.update()
        before=d.snapshot()
        assert str(dialog.notebook.select())==str(dialog.comparison)
        assert dialog.left_page.page_no==2 and dialog.right_page.page_no==3
        for preview in (dialog.left_page,dialog.right_page): assert_complete_page(preview)
        dialog.notebook.select(dialog.browser)
        dialog.browse_at(5)
        assert dialog.page()==3 and dialog.browser_number==6 and d.snapshot()==before
        dialog.left_page.fit_width()
        dialog.skip_current()
        root.update()
        assert str(dialog.notebook.select())==str(dialog.comparison)
        assert dialog.left_page.page_no==4 and dialog.right_page.page_no==5
        assert dialog.left_page.fit_mode==dialog.right_page.fit_mode=="page"
        for preview in (dialog.left_page,dialog.right_page): assert_complete_page(preview)
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()
