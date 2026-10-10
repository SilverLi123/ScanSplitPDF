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


def compute_groups(separators: set[int], n_pages: int, split_starts: set[int] | None = None) -> list[tuple[int, int]]:
    """返回 [(start, end)]，1 起始、含端点、按页序排列、互不重叠、均非空。

    separators：空白分隔页（页本身不进入任何组）；
    split_starts：拆分边界——新文档从该页开始，页本身保留在组内
    （用于无空白页场景的智能补拆，绝不丢页）。
    """
    split_starts = split_starts or set()
    groups: list[tuple[int, int]] = []
    cur_start: int | None = None
    for p in range(1, n_pages + 1):
        if p in separators:
            if cur_start is not None:
                groups.append((cur_start, p - 1))
                cur_start = None
        elif p in split_starts:
            if cur_start is not None:
                groups.append((cur_start, p - 1))
            cur_start = p
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
    """按当前分隔页集合重算分组；尽量保留已填写的标题/单位（v1.8）。

    迁移规则：
    - 起止页完全相同 → 原组原样保留（原有行为）；
    - 分组边界调整后，新组与旧组存在唯一对应（新组只与一个旧组重叠，
      且该旧组也只对应这一个新组）→ 迁移标题/单位/候选/确认状态，
      人工编辑不丢失；
    - 对应关系存在歧义（一对多）→ 不猜标题，合并旧组候选供复核，
      标记待确认（绝不静默丢弃人工编辑）。
    迁移与歧义说明写入 doc.group_migrate_notes，由 UI 转述给用户。
    """
    old_list = list(doc.groups)
    new_ranges = compute_groups(doc.separators, doc.page_count, doc.split_starts)
    old_by_range = {(g.start, g.end): g for g in old_list}

    # 新组 ↔ 旧组 重叠关系
    sources_of: dict[tuple[int, int], list[Group]] = {}
    targets_of: dict[int, list[tuple[int, int]]] = {}
    for rng in new_ranges:
        srcs = [o for o in old_list if o.start <= rng[1] and o.end >= rng[0]]
        sources_of[rng] = srcs
        for o in srcs:
            targets_of.setdefault(id(o), []).append(rng)

    notes: list[str] = []
    new: list[Group] = []
    for start, end in new_ranges:
        prev = old_by_range.get((start, end))
        if prev is not None:
            new.append(prev)
            continue
        g = Group(start=start, end=end)
        srcs = sources_of[(start, end)]
        if len(srcs) == 1 and len(targets_of[id(srcs[0])]) == 1:
            src = srcs[0]
            _migrate_group_fields(src, g)
            if src.title.strip() or src.unit.strip():
                notes.append(
                    f"第 {start}-{end} 页：已沿用原第 {src.start}-{src.end} 页分组的标题/单位"
                )
        elif srcs:
            # 歧义：旧组被拆到多个新组（或新组横跨多个旧组），不能替用户做主。
            # 候选按字段分桶保留供复核，标题/单位留空、标记待确认。
            g.title_candidates = _cands_of(srcs, "title")
            g.unit_candidates = _cands_of(srcs, "unit")
            g.title_confirmed = False
            g.unit_confirmed = False
            notes.append(
                f"第 {start}-{end} 页：分组边界调整后与原分组对应关系不唯一，"
                "已保留命名候选，请人工复核标题/单位"
            )
        new.append(g)
    doc.groups = new
    doc.group_migrate_notes = notes
    return new


def _migrate_group_fields(src: Group, dst: Group) -> None:
    dst.title = src.title
    dst.unit = src.unit
    dst.title_conf = src.title_conf
    dst.unit_conf = src.unit_conf
    dst.title_candidates = list(src.title_candidates)
    dst.unit_candidates = list(src.unit_candidates)
    dst.title_confirmed = src.title_confirmed
    dst.unit_confirmed = src.unit_confirmed


def _cands_of(groups: list[Group], which: str) -> list:
    out, seen = [], set()
    for g in groups:
        for c in getattr(g, f"{which}_candidates"):
            key = (c.get("text"), c.get("source"))
            if c.get("text") and key not in seen:
                seen.add(key)
                out.append(dict(c))
    return out
