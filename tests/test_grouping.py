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

    # v1.9 A0：auto_group 默认按上下文业务属性分组（无上下文结论 → 空白候选保留）。
    # 显式用旧行为（context_aware=False）验证"空白候选作为分隔"的基础分组逻辑。
    groups = auto_group(doc, context_aware=False)
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


def test_group_names_migrate_on_unique_mapping(make_scan):
    """v1.8：分组边界调整后，唯一对应关系的旧组人工命名应迁移（不静默丢失）。"""
    pdf = make_scan("n.pdf", ["text", "text", "text"])
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    doc.pages[2].separator = True
    groups = refresh_groups(doc)
    groups[0].title = "报告"
    groups[0].unit = "教务处"
    groups[0].title_confirmed = True  # 人工编辑
    # 拆分点从第 3 页移到第 1 页：旧组 (1,2) 与新组 (2,3) 唯一对应
    doc.pages[2].separator = False
    doc.pages[0].separator = True
    refresh_groups(doc)
    assert [(g.start, g.end) for g in doc.groups] == [(2, 3)]
    g = doc.groups[0]
    assert g.title == "报告" and g.unit == "教务处"
    assert g.title_confirmed is True  # 人工确认状态随迁移保留
    assert any("沿用原第 1-2 页分组" in n for n in doc.group_migrate_notes)


def test_compute_groups_split_starts_keep_pages():
    """拆分边界（split_before）不吞页：第 3 页起新文档，页面全部保留。"""
    from scansplit.grouping import compute_groups

    assert compute_groups(set(), 4, {3}) == [(1, 2), (3, 4)]
    # 与空白分隔页混用
    assert compute_groups({5}, 6, {3}) == [(1, 2), (3, 4), (6, 6)]
    # 首页作为拆分起点无效果（第 1 页本来就是文档起点）
    assert compute_groups(set(), 2, {1}) == [(1, 2)]
    # 连续拆分起点合并为多个非空组
    assert compute_groups(set(), 5, {3, 4}) == [(1, 2), (3, 3), (4, 5)]


def test_separator_and_split_start_mixed_doc():
    """空白分隔页与拆分边界混用：各页归属正确、无一丢失。"""
    from scansplit.grouping import compute_groups

    # 6 页：第 3 页为空白分隔页，第 5 页为拆分边界
    groups = compute_groups({3}, 6, {5})
    assert groups == [(1, 2), (4, 4), (5, 6)]
    # 无一页丢失（(4,4) 单页组展开时注意去重）
    assert sorted({p for g in groups for p in g}) == [1, 2, 4, 5, 6]


def test_migration_ambiguous_split_keeps_candidates(make_scan):
    """v1.8：旧组被一拆为二时存在歧义——不猜标题，候选保留 + 待确认，绝不静默丢失。"""
    pdf = make_scan("am.pdf", ["text"] * 6)
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    groups = refresh_groups(doc)  # 单组 (1,6)
    groups[0].title = "长报告"
    groups[0].unit = "教务处"
    groups[0].title_candidates = [
        {"text": "长报告", "confidence": 0.9, "source": "标题", "reason": ""}
    ]
    groups[0].unit_candidates = [
        {"text": "教务处", "confidence": 0.8, "source": "落款", "reason": ""}
    ]
    # 第 4 页改为分隔页：旧组 (1,6) 拆成 (1,3) 与 (5,6)（第 4 页被排除），对应关系歧义
    doc.pages[3].separator = True
    refresh_groups(doc)
    assert [(g.start, g.end) for g in doc.groups] == [(1, 3), (5, 6)]
    for g in doc.groups:
        assert g.title == "" and g.unit == ""  # 不替用户做主
        assert g.title_confirmed is False and g.unit_confirmed is False
        assert g.need_confirm is True
        assert g.title_candidates and g.unit_candidates  # 候选保留供复核
        assert g.title_candidates[0]["text"] == "长报告"
    assert any("对应关系不唯一" in n for n in doc.group_migrate_notes)


def test_migration_two_old_groups_merge_unique(make_scan):
    """v1.8：两个旧组合并为一个新组（取消分隔页）——歧义时候选合并保留。"""
    pdf = make_scan("mg.pdf", ["text", "text", "text"])
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    doc.pages[1].separator = True
    groups = refresh_groups(doc)
    assert [(g.start, g.end) for g in groups] == [(1, 1), (3, 3)]
    groups[0].title = "前半"
    groups[1].title = "后半"
    groups[0].title_candidates = [{"text": "前半", "confidence": 0.9, "source": "标题", "reason": ""}]
    groups[1].title_candidates = [{"text": "后半", "confidence": 0.9, "source": "标题", "reason": ""}]
    # 取消分隔页 → 新组 (1,3) 同时重叠两个旧组 → 歧义
    doc.pages[1].separator = False
    refresh_groups(doc)
    assert [(g.start, g.end) for g in doc.groups] == [(1, 3)]
    g = doc.groups[0]
    assert g.need_confirm is True
    assert g.title == "" and g.unit == ""
    assert {c["text"] for c in g.title_candidates} == {"前半", "后半"}
    assert any("对应关系不唯一" in n for n in doc.group_migrate_notes)

