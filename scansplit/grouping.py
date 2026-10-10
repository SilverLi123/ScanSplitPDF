"""分组：由分隔页集合推导文档组（纯函数，便于测试）。

规则：
- 空白页仅是"候选分隔符"，由用户确认后成为分隔页；
- 连续分隔页合并为一个分隔区域；
- 开头/结尾的分隔页不产生空组；
- 未被标记为分隔页的空白页保留在文档内部；
- 任何组至少包含 1 页。
"""

from __future__ import annotations

from .model import BLANK, MAYBE, Group, ScanDocument


def compute_groups(separators: set[int], n_pages: int) -> list[tuple[int, int]]:
    """返回 [(start, end)]，1 起始、含端点、按页序排列、互不重叠、均非空。"""
    groups: list[tuple[int, int]] = []
    cur_start: int | None = None
    for p in range(1, n_pages + 1):
        if p in separators:
            if cur_start is not None:
                groups.append((cur_start, p - 1))
                cur_start = None
        elif cur_start is None:
            cur_start = p
    if cur_start is not None:
        groups.append((cur_start, n_pages))
    return groups


def blank_candidates(results) -> set[int]:
    """判定为"空白"的页码（1 起始）。"""
    return {i + 1 for i, r in enumerate(results) if r.verdict == BLANK}


def maybe_candidates(results) -> set[int]:
    """判定为"疑似空白"的页码（1 起始），需人工确认。"""
    return {i + 1 for i, r in enumerate(results) if r.verdict == MAYBE}


def auto_group(doc: ScanDocument) -> list[Group]:
    """把所有空白候选页标记为分隔页并重算分组。"""
    for p in doc.pages:
        if p.blank is not None and p.blank.verdict == BLANK:
            p.separator = True
    return refresh_groups(doc)


def refresh_groups(doc: ScanDocument) -> list[Group]:
    """按当前分隔页集合重算分组；尽量保留已填写的标题/单位。"""
    old = {(g.start, g.end): g for g in doc.groups}
    new: list[Group] = []
    for start, end in compute_groups(doc.separators, doc.page_count):
        prev = old.get((start, end))
        new.append(
            prev if prev else Group(start=start, end=end)
        )
    doc.groups = new
    return new
