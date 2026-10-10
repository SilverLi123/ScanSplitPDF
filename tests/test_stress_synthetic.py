# -*- coding: utf-8 -*-
"""v1.9 任务2 的合成等价回归（无私人员样本的机器也能运行）。

复现 81 页压力测试的三类结构性陷阱（不依赖 OCR）：
1. 文档内部空白页（如样本 p23/p58）**不得**被 auto_group 误当拆分点；
2. 无空白分隔的文档边界（如样本 p38/p39）auto_group 无法拆分 —— 行为基线；
3. 空白检测只拆空白页：稀疏内容页（如样本 p72）不得判空白。

行为变更说明（v1.9 阶段 A0）：
    本文件原第 1 条把"内部空白页被误拆"固化为基线。A0 已修复该缺陷——
    auto_group 默认启用上下文判定（context_aware=True），无上下文证据的空白页
    一律保留（verdict=uncertain/internal_blank），不再静默丢页。故此处更新为
    "无上下文判定时保留内部空白页"，并以 context_aware=False 单独固化旧行为，
    便于回归定位。第 2、3 条行为未变。
    任何进一步改变上述行为的改动必须显式更新本测试并说明理由。
"""

import pytest


def test_internal_blank_default_retained_in_grouping(make_scan):
    """A0：未经上下文判定的内部空白页默认保留（不再被 auto_group 静默丢页）。

    合成样本三页均为"有内容"渲染，p3 被判空白；没有 OCR 上下文信号时，
    auto_group(context_aware=True) 必须保守保留该空白页 → 单组 (1,5)。
    """
    from scansplit import blankdetect, grouping, loader
    from scansplit.model import ScanDocument
    from scansplit.render import PageRenderer

    scan = make_scan("inner_blank.pdf", ["text", "text", "blank", "text", "text"])
    reader, _ = loader.open_document(scan)
    doc = ScanDocument(scan, reader, [])
    renderer = PageRenderer(scan)
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(),
            lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        doc.status = "analyzed"
        groups = grouping.auto_group(doc)  # context_aware=True（默认）
        # 无上下文证据 → 空白页保留在文档内，不产生拆分
        assert [(g.start, g.end) for g in groups] == [(1, 5)]
    finally:
        renderer.close()


def test_legacy_auto_group_splits_on_any_blank(make_scan):
    """兼容基线：context_aware=False 时恢复旧行为（所有空白候选作分隔页）。

    仅用于回归定位旧行为，生产路径不启用。
    """
    from scansplit import blankdetect, grouping, loader
    from scansplit.model import ScanDocument
    from scansplit.render import PageRenderer

    scan = make_scan("legacy_blank.pdf", ["text", "text", "blank", "text", "text"])
    reader, _ = loader.open_document(scan)
    doc = ScanDocument(scan, reader, [])
    renderer = PageRenderer(scan)
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(),
            lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        doc.status = "analyzed"
        groups = grouping.auto_group(doc, context_aware=False)
        assert [(g.start, g.end) for g in groups] == [(1, 2), (4, 5)]
    finally:
        renderer.close()


def test_no_blank_boundary_not_split(make_scan):
    """无空白分隔的文档边界不会自动拆分（压力样本 p38/p39 的行为基线）。"""
    from scansplit import blankdetect, grouping, loader
    from scansplit.model import ScanDocument
    from scansplit.render import PageRenderer

    # 两份"文档"直接拼接，中间没有空白页
    scan = make_scan("no_sep.pdf", ["text", "text", "text", "text"])
    reader, _ = loader.open_document(scan)
    doc = ScanDocument(scan, reader, [])
    renderer = PageRenderer(scan)
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(),
            lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        doc.status = "analyzed"
        groups = grouping.auto_group(doc)
        assert [(g.start, g.end) for g in groups] == [(1, 4)]  # 漏拆
    finally:
        renderer.close()


def test_separator_and_end_blanks_split_correctly(make_scan):
    """正常分隔/末尾空白页拆分正确。

    A0 语义：文件末尾的空白页（之后无内容页）无歧义地判为边界分隔并排除；
    文件中部无上下文证据的空白页保守保留。样本 p2/p5 均为空白；p5 之后仍有
    内容页 p6，故两者都属"无上下文证据"→ 均保留（单组 1-6）。若 p5 是文件
    末尾空白（无后续内容页），则应作为边界分隔被排除。
    """
    from scansplit import blankdetect, grouping, loader
    from scansplit.model import ScanDocument
    from scansplit.render import PageRenderer

    scan = make_scan(
        "seps.pdf",
        ["text", "blank", "text", "text", "blank"])
    reader, _ = loader.open_document(scan)
    doc = ScanDocument(scan, reader, [])
    renderer = PageRenderer(scan)
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(),
            lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        doc.status = "analyzed"
        # 末尾空白页（p5 之后无内容页）为无歧义边界分隔
        last_blank = [st.src_index for st in doc.pages
                      if st.blank is not None and st.blank.verdict == "blank"][-1]
        doc.pages[last_blank - 1].blank_context = "separator"
        groups = grouping.auto_group(doc)
        # p2 保留；末尾空白页作为分隔页排除 → 文档在倒数第二页结束
        assert [(g.start, g.end) for g in groups] == [(1, 4)]
    finally:
        renderer.close()


def test_internal_blank_page_retained_in_export_mapping(make_scan, tmp_path):
    """内部空白页（压力样本 p23/p58 场景）不得从导出校验集合消失。

    A0 语义：auto_group 默认不再把内部空白页误标为分隔页；本测试以显式
    人工分隔标记验证导出映射与业务完整性双层校验：
    - 人工显式标记分隔 → 该页排除，覆盖率/业务完整性仍需通过（人工定义的全集）；
    - 人工取消分隔标记 → 该页必须重新进入导出映射并出现在输出 PDF 中。
    """
    from pypdf import PdfReader

    from scansplit import exporter, grouping, verify
    from scansplit.model import ScanDocument
    from scansplit.loader import open_document

    scan = make_scan("ib_export.pdf", ["text", "text", "blank", "text", "text"])
    reader, _ = open_document(scan)
    doc = ScanDocument(scan, reader, [])

    # 阶段1：人工显式把内部空白页 p3 标记为分隔页 → 输出映射 [1,2,4,5]
    doc.pages[2].blank_context = "separator"
    doc.pages[2].separator = True
    grouping.refresh_groups(doc)
    assert doc.separators == {3}
    opts = exporter.ExportOptions(template="{title}_{unit}", out_dir=tmp_path, strict=False)
    doc.groups[0].title, doc.groups[0].unit = "文档A", "单位A"
    doc.groups[1].title, doc.groups[1].unit = "文档B", "单位B"
    report = exporter.export_documents([doc], opts)
    assert len(report.succeeded) == 2 and not report.failed
    page_map = sorted(p for r in report.succeeded for p in r.pages)
    assert page_map == [1, 2, 4, 5]  # p3 被明确排除（separator）
    assert not report.coverage_issues
    assert not exporter.business_integrity_issues(doc)  # 每页已导出或确属分隔

    # 阶段2：人工取消分隔标记（压力测试复核路径）→ p3 必须回到输出
    doc.pages[2].blank_context = ""
    doc.pages[2].separator = False
    grouping.refresh_groups(doc)
    assert [(g.start, g.end) for g in doc.groups] == [(1, 5)]
    doc.groups[0].title, doc.groups[0].unit = "完整文档", "单位C"
    out2 = tmp_path / "full"
    out2.mkdir()
    opts2 = exporter.ExportOptions(template="{title}_{unit}", out_dir=out2, strict=False)
    report2 = exporter.export_documents([doc], opts2)
    assert len(report2.succeeded) == 1 and not report2.failed
    page_map2 = report2.succeeded[0].pages
    assert page_map2 == [1, 2, 3, 4, 5]  # 逐页映射：内部空白页保留
    assert not report2.coverage_issues
    # 双重校验：输出 PDF 实际页数与映射一致，verify_mapping 无错误
    out_pdf = report2.succeeded[0].path
    assert len(PdfReader(str(out_pdf)).pages) == 5
    assert verify.verify_mapping(scan, out_pdf, page_map2) == []


def test_uncertain_blank_never_silently_dropped(make_scan, tmp_path):
    """A0 核心红线：未经用户确认的 uncertain 空白页不得被排除/丢失。

    business_integrity_issues 要求每个原始页必须"已导出"或"经用户确认排除"。
    这里把空白页标为 uncertain 且不置 separator，导出后该页必须出现且校验通过。
    """
    from pypdf import PdfReader

    from scansplit import exporter, grouping, verify
    from scansplit.model import ScanDocument
    from scansplit.loader import open_document

    scan = make_scan("unc.pdf", ["text", "blank", "text"])
    reader, _ = open_document(scan)
    doc = ScanDocument(scan, reader, [])
    doc.pages[1].blank_context = "uncertain"
    doc.pages[1].separator = False
    grouping.refresh_groups(doc)
    doc.groups[0].title, doc.groups[0].unit = "文档", "单位"
    opts = exporter.ExportOptions(template="{title}_{unit}", out_dir=tmp_path, strict=False)
    report = exporter.export_documents([doc], opts)
    assert len(report.succeeded) == 1
    assert report.succeeded[0].pages == [1, 2, 3]
    assert not report.coverage_issues
    assert not exporter.business_integrity_issues(doc)
    out_pdf = report.succeeded[0].path
    assert len(PdfReader(str(out_pdf)).pages) == 3
    assert verify.verify_mapping(scan, out_pdf, [1, 2, 3]) == []
