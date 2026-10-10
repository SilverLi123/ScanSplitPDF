# -*- coding: utf-8 -*-
"""v1.9 任务2 的合成等价回归（无私人员样本的机器也能运行）。

复现 81 页压力测试的三类结构性陷阱（不依赖 OCR）：
1. 文档内部空白页（如样本 p23/p58）会被 auto_group 误当拆分点 —— 当前行为基线；
2. 无空白分隔的文档边界（如样本 p38/p39）auto_group 无法拆分 —— 当前行为基线；
3. 空白检测只拆空白页：稀疏内容页（如样本 p72）不得判空白。
此文件固化行为基线：任何改变上述行为的改动必须显式更新本测试并说明理由。
"""

import pytest


def test_internal_blank_causes_false_split(make_scan):
    """内部空白页会被误拆（压力样本 p23/p58 的行为基线）。"""
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
        groups = grouping.auto_group(doc)
        # 内部空白页 p3 被当作拆分点 → (1,2),(4,5)，与真实样本行为一致
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
    """正常分隔/末尾空白页拆分正确（压力样本 15 张空白的行为基线）。"""
    from scansplit import blankdetect, grouping, loader
    from scansplit.model import ScanDocument
    from scansplit.render import PageRenderer

    scan = make_scan(
        "seps.pdf",
        ["text", "blank", "text", "text", "blank", "text"])
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
        # 分隔页被排除在组外；末尾空白页构成末组
        assert [(g.start, g.end) for g in groups] == [(1, 1), (3, 4), (6, 6)]
    finally:
        renderer.close()


def test_internal_blank_page_retained_in_export_mapping(make_scan, tmp_path):
    """内部空白页（压力样本 p23/p58 场景）不得从导出校验集合消失。

    自动分组会把内部空白页标记为分隔页（从输出中排除）——这是当前行为；
    但人工取消分隔标记后，该页必须重新进入导出映射并出现在输出 PDF 中。
    本测试按"标准答案"逐页核对输出映射。
    """
    from pypdf import PdfReader

    from scansplit import exporter, grouping, verify
    from scansplit.model import ScanDocument
    from scansplit.loader import open_document

    scan = make_scan("ib_export.pdf", ["text", "text", "blank", "text", "text"])
    reader, _ = open_document(scan)
    doc = ScanDocument(scan, reader, [])

    # 阶段1：自动分组把内部空白页 p3 标记为分隔页 → 输出映射 [1,2,4,5]
    doc.pages[2].separator = True
    grouping.refresh_groups(doc)
    assert doc.separators == {3}
    opts = exporter.ExportOptions(template="{title}_{unit}", out_dir=tmp_path, strict=False)
    doc.groups[0].title, doc.groups[0].unit = "文档A", "单位A"
    doc.groups[1].title, doc.groups[1].unit = "文档B", "单位B"
    report = exporter.export_documents([doc], opts)
    assert len(report.succeeded) == 2 and not report.failed
    page_map = sorted(p for r in report.succeeded for p in r.pages)
    assert page_map == [1, 2, 4, 5]  # p3 被排除（当前行为基线）
    assert not report.coverage_issues  # 覆盖率以"非分隔页"为全集，仍通过

    # 阶段2：人工取消分隔标记（压力测试复核路径）→ p3 必须回到输出
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


def test_internal_blank_page_retained_in_export_mapping(make_scan, tmp_path):
    """内部空白页（压力样本 p23/p58 场景）不得从导出校验集合消失。

    自动分组会把内部空白页标记为分隔页（从输出中排除）——这是当前行为；
    但人工取消分隔标记后，该页必须重新进入导出映射并出现在输出 PDF 中。
    本测试按"标准答案"逐页核对输出映射。
    """
    from pypdf import PdfReader

    from scansplit import exporter, grouping, verify
    from scansplit.model import ScanDocument
    from scansplit.loader import open_document

    scan = make_scan("ib_export.pdf", ["text", "text", "blank", "text", "text"])
    reader, _ = open_document(scan)
    doc = ScanDocument(scan, reader, [])

    # 阶段1：自动分组把内部空白页 p3 标记为分隔页 → 输出映射 [1,2,4,5]
    doc.pages[2].separator = True
    grouping.refresh_groups(doc)
    assert doc.separators == {3}
    opts = exporter.ExportOptions(template="{title}_{unit}", out_dir=tmp_path, strict=False)
    doc.groups[0].title, doc.groups[0].unit = "文档A", "单位A"
    doc.groups[1].title, doc.groups[1].unit = "文档B", "单位B"
    report = exporter.export_documents([doc], opts)
    assert len(report.succeeded) == 2 and not report.failed
    page_map = sorted(p for r in report.succeeded for p in r.pages)
    assert page_map == [1, 2, 4, 5]  # p3 被排除（当前行为基线）
    assert not report.coverage_issues  # 覆盖率以"非分隔页"为全集，仍通过

    # 阶段2：人工取消分隔标记（压力测试复核路径）→ p3 必须回到输出
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
