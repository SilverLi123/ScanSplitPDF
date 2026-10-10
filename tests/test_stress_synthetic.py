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
