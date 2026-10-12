"""User regressions: long export descriptions and form pagination in titles."""
from types import SimpleNamespace

import pytest

from scansplit.model import Group
from scansplit.ocr import OcrLine
from scansplit.ocrfields import extract_title
from tests.test_ui_smoke import _make_root


def line(text, y=100, h=30):
    return OcrLine(text, .98, [[200, y], [800, y], [800, y+h], [200, y+h]])


@pytest.mark.parametrize("metadata", ["页次：1", "页次: 12", "页 次 ： 1", "页码：1/3", "第1页", "第 1 页 共 3 页", "1", "2026年10月"])
def test_title_does_not_join_pagination(metadata):
    original = [line("实验教学中心巡查记录表"), line(metadata, 140)]
    candidates = extract_title(original, 1000, 1400)
    assert candidates[0].text == "实验教学中心巡查记录表"
    assert all(metadata not in c.text for c in candidates)
    assert original[1].text == metadata


@pytest.mark.parametrize("suffix", ["页次：1", " 页码:12/30", "页 次 ： 1"])
def test_same_box_pagination_suffix(suffix):
    original = line("实验教学中心巡查记录表" + suffix)
    assert extract_title([original], 1000, 1400)[0].text == "实验教学中心巡查记录表"
    assert original.text.endswith(suffix)


def test_real_multiline_title_and_page_related_title_preserved():
    assert extract_title([line("2026年度实验室"), line("安全检查报告", 140)], 1000, 1400)[0].text == "2026年度实验室安全检查报告"
    assert extract_title([line("页次管理规定")], 1000, 1400)[0].text == "页次管理规定"


def export_dialog(root, blocked=False, pending=False):
    from scansplit.ui_widgets import ExportSummaryDialog
    groups = [Group(i+1, i+1, title=f"报告{i+1}", unit="实验中心", title_confirmed=not pending) for i in range(30)]
    docs = [SimpleNamespace(groups=groups)]
    long_name = "%E7%BA%AF%E5%9B%BE%E7%89%87PDF_" * 10
    excluded = [f"{long_name} 第 {i} 页（依据：明确空白）" for i in range(1, 21)]
    stats = dict(n_groups=30, n_confirmed=30, n_pending=0, excluded=excluded, dup={})
    dlg = ExportSummaryDialog(root, docs, stats, [], ["页面完整性失败"] if blocked else [],
                              name_of=lambda d, i, g: g.title + ".pdf", integrity_blocked=blocked)
    dlg.geometry("800x520")
    root.update()
    return dlg, excluded


def test_export_buttons_visible_and_all_exclusions_scroll():
    root = _make_root()
    root.deiconify()
    try:
        dlg, excluded = export_dialog(root)
        button = dlg.export_button
        assert button.winfo_ismapped()
        assert button.winfo_rooty() + button.winfo_height() <= dlg.winfo_rooty() + dlg.winfo_height()
        assert dlg.tree.winfo_height() > 80
        assert all(item in dlg.details.get("1.0", "end") for item in excluded)
        before = dlg.details.yview()
        dlg.details.event_generate("<MouseWheel>", delta=-120)
        root.update()
        assert dlg.details.yview()[0] > before[0]
        dlg.tree.yview_moveto(1)
        assert dlg.tree.yview()[0] > 0
        dlg._confirm_strict()
        assert dlg.result["confirmed"] and len(dlg.result["items"]) == 30
    finally:
        root.destroy()


def test_integrity_block_has_no_export_button():
    root = _make_root()
    root.deiconify()
    try:
        dlg, _ = export_dialog(root, blocked=True)
        assert not hasattr(dlg, "export_button")
        assert "页面完整性失败" in dlg.details.get("1.0", "end")
        assert not dlg.result["confirmed"]
        dlg.event_generate("<Escape>")
        root.update()
        if dlg.winfo_exists():
            dlg._cancel()
        assert not dlg.result["confirmed"]
    finally:
        root.destroy()


def test_pending_name_cannot_export(monkeypatch):
    from scansplit import ui_widgets
    warnings = []
    monkeypatch.setattr(ui_widgets.messagebox, "showwarning", lambda *args, **kw: warnings.append(args))
    root = _make_root()
    root.deiconify()
    try:
        dlg, _ = export_dialog(root, pending=True)
        dlg._confirm_strict()
        assert warnings and not dlg.result["confirmed"]
        assert dlg.winfo_exists()
    finally:
        root.destroy()
