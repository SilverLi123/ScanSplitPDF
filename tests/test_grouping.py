"""分组测试：连续空白合并、首尾空白、内部空白保留、无空白文档、手动增删拆分点。"""

from scansplit.grouping import (
    auto_group,
    blank_candidates,
    compute_groups,
    maybe_candidates,
    refresh_groups,
)
from scansplit.loader import open_document
from scansplit.model import BLANK, CONTENT, MAYBE, BlankResult, ScanDocument


def test_compute_groups_basic():
    assert compute_groups({4, 9}, 12) == [(1, 3), (5, 8), (10, 12)]


def test_consecutive_separators_merge():
    assert compute_groups({4, 5, 6, 10}, 12) == [(1, 3), (7, 9), (11, 12)]


def test_leading_and_trailing_separators():
    assert compute_groups({1, 2, 7, 8}, 8) == [(3, 6)]
    assert compute_groups({1}, 3) == [(2, 3)]
    assert compute_groups({3}, 3) == [(1, 2)]


def test_no_separators_single_group():
    assert compute_groups(set(), 5) == [(1, 5)]


def test_all_separators_no_groups():
    assert compute_groups({1, 2, 3}, 3) == []


def test_no_empty_documents_ever():
    for n in range(1, 12):
        for s in ({i for i in range(1, n + 1)}, set(), {n}, {1, n}):
            for start, end in compute_groups(s, n):
                assert start <= end
                assert not (set(range(start, end + 1)) & s)


def test_internal_blank_kept_when_not_separator():
    """文档内部空白页：不标记为分隔页则保留在组内。"""
    assert compute_groups({4}, 6) == [(1, 3), (5, 6)]  # 第 2 页空白但非分隔页 → 留在组 1


def test_auto_group_and_manual_toggle(make_scan):
    pdf = make_scan("g.pdf", ["text", "text", "blank", "text", "blank", "text"])
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    results = [BlankResult(verdict=CONTENT), BlankResult(verdict=CONTENT),
               BlankResult(verdict=BLANK), BlankResult(verdict=CONTENT),
               BlankResult(verdict=BLANK), BlankResult(verdict=CONTENT)]
    for p, r in zip(doc.pages, results):
        p.blank = r
    assert blank_candidates(results) == {3, 5}

    groups = auto_group(doc)
    assert [(g.start, g.end) for g in groups] == [(1, 2), (4, 4), (6, 6)]

    # 手动取消第 5 页分隔（它是文档内部的空白页）
    doc.pages[4].separator = False
    groups = refresh_groups(doc)
    assert [(g.start, g.end) for g in groups] == [(1, 2), (4, 6)]

    # 手动把第 2 页改为分隔页（新增拆分点）
    doc.pages[1].separator = True
    groups = refresh_groups(doc)
    assert [(g.start, g.end) for g in groups] == [(1, 1), (4, 6)]


def test_maybe_pages_not_auto_separator(make_scan):
    pdf = make_scan("m.pdf", ["text", "stamp", "text"])
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    results = [BlankResult(verdict=CONTENT), BlankResult(verdict=MAYBE),
               BlankResult(verdict=CONTENT)]
    for p, r in zip(doc.pages, results):
        p.blank = r
    assert blank_candidates(results) == set()
    assert maybe_candidates(results) == {2}
    groups = auto_group(doc)  # 疑似页绝不自动成为分隔页
    assert [(g.start, g.end) for g in groups] == [(1, 3)]


def test_group_names_preserved_across_refresh(make_scan):
    pdf = make_scan("n.pdf", ["text", "blank", "text"])
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    doc.pages[1].separator = True
    groups = refresh_groups(doc)
    groups[0].title = "报告"
    groups[0].unit = "教务处"
    doc.pages[1].separator = False
    doc.pages[0].separator = True
    refresh_groups(doc)
    # 原组 (1,1) 变成了 (2,2)… 名称不应错误迁移到不同的页范围
    assert all(g.title == "" for g in doc.groups)
