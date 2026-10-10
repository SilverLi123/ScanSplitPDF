"""数据模型：页面状态、空白判定、文档分组与快照（用于撤销）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pypdf import PdfReader

BLANK = "blank"
MAYBE = "maybe"
CONTENT = "content"

VERDICT_LABEL = {BLANK: "空白", MAYBE: "疑似空白", CONTENT: "有内容"}


@dataclass
class BlankResult:
    verdict: str = CONTENT
    confidence: float = 0.0
    signals: dict = field(default_factory=dict)

    @property
    def is_blank(self) -> bool:
        return self.verdict == BLANK

    @property
    def label(self) -> str:
        return VERDICT_LABEL.get(self.verdict, self.verdict)

    def reason_cn(self) -> str:
        """人类可读的判断依据，用于 UI 展示。"""
        s = self.signals
        parts = [
            f"墨迹比 {s.get('ink_ratio', 0):.2f}%",
            f"深色比 {s.get('dark_ratio', 0):.2f}%",
            f"边缘墨迹 {s.get('edge_ink', 0):.1f}%",
            f"内容区域 {s.get('bbox_frac', 0):.1f}%",
        ]
        if s.get("has_text"):
            parts.append("含文本层")
        return f"{self.label}（置信度 {self.confidence:.2f}）：" + "、".join(parts)


@dataclass
class PageState:
    src_index: int  # 1 起始
    rotation: int = 0  # 目标旋转角度 0/90/180/270（导出时写入 /Rotate）
    blank: Optional[BlankResult] = None
    separator: bool = False  # 当前是否作为分隔页（该页本身是空白分隔页，不进入任何组）
    split_before: bool = False  # 该页是新文档起点（拆分边界，页本身保留在组内）

    def snapshot(self) -> dict:
        return {
            "src_index": self.src_index,
            "rotation": self.rotation,
            "blank": (self.blank.verdict, self.blank.confidence, dict(self.blank.signals))
            if self.blank
            else None,
            "separator": self.separator,
            "split_before": self.split_before,
        }

    @staticmethod
    def restore(d: dict) -> "PageState":
        ps = PageState(src_index=d["src_index"])
        ps.rotation = d["rotation"]
        ps.separator = d["separator"]
        ps.split_before = bool(d.get("split_before", False))
        if d["blank"]:
            v, c, s = d["blank"]
            ps.blank = BlankResult(verdict=v, confidence=c, signals=s)
        return ps


@dataclass
class Group:
    start: int  # 1 起始、含端点
    end: int  # 1 起始、含端点
    title: str = ""
    unit: str = ""
    title_conf: float | None = None  # None = 未自动提取
    unit_conf: float | None = None
    title_candidates: list = field(default_factory=list)  # [FieldCandidate]
    unit_candidates: list = field(default_factory=list)
    # 字段级确认状态（v1.8）：True = 该字段无需人工复核。
    # 默认 True：无 OCR 结果时不产生"待确认"噪音；自动命名后按置信度回落。
    title_confirmed: bool = True
    unit_confirmed: bool = True

    @property
    def page_count(self) -> int:
        return self.end - self.start + 1

    @property
    def pages(self) -> list[int]:
        return list(range(self.start, self.end + 1))

    @property
    def need_confirm(self) -> bool:
        """任一字段未确认即整组待确认（字段级状态的派生值）。"""
        return not (self.title_confirmed and self.unit_confirmed)

    @need_confirm.setter
    def need_confirm(self, value: bool) -> None:
        """兼容旧代码/旧快照的整组赋值：True = 两个字段都标记待确认。"""
        self.title_confirmed = not value
        self.unit_confirmed = not value

    def snapshot(self) -> dict:
        def _norm(cands: list) -> list:
            # OCR 命名后候选以 dict 存入（auto_name_groups），其余时刻为 FieldCandidate
            return [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in cands]

        return {
            "start": self.start, "end": self.end, "title": self.title, "unit": self.unit,
            "title_conf": self.title_conf, "unit_conf": self.unit_conf,
            "title_candidates": _norm(self.title_candidates),
            "unit_candidates": _norm(self.unit_candidates),
            "title_confirmed": self.title_confirmed,
            "unit_confirmed": self.unit_confirmed,
            "need_confirm": self.need_confirm,  # 兼容旧版读取
        }

    @staticmethod
    def restore(d: dict) -> "Group":
        g = Group(start=d["start"], end=d["end"], title=d.get("title", ""),
                  unit=d.get("unit", ""), title_conf=d.get("title_conf"),
                  unit_conf=d.get("unit_conf"))
        # 字段级确认状态；旧快照仅有 need_confirm 时按整组语义还原
        tc = d.get("title_confirmed")
        uc = d.get("unit_confirmed")
        if tc is None or uc is None:
            tc = uc = not d.get("need_confirm", False)
        g.title_confirmed = bool(tc)
        g.unit_confirmed = bool(uc)
        # 候选以 dict 形态存储（与 auto_name_groups 写入、UI 面板读取一致）
        g.title_candidates = [dict(x) for x in d.get("title_candidates", [])]
        g.unit_candidates = [dict(x) for x in d.get("unit_candidates", [])]
        return g


class ScanDocument:
    """一个待处理的源 PDF。reader 常驻内存用于读取与导出。"""

    def __init__(self, path: str | Path, reader: PdfReader, warnings: list[str] | None = None):
        self.path = Path(path)
        self.reader = reader
        self.warnings = list(warnings or [])
        self.pages: list[PageState] = [
            PageState(src_index=i + 1) for i in range(len(reader.pages))
        ]
        self.groups: list[Group] = []
        self.group_migrate_notes: list[str] = []  # refresh_groups 的迁移/歧义说明
        self.status = "loaded"  # loaded | analyzed | grouped | exported | error
        self.error: Optional[str] = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def separators(self) -> set[int]:
        return {p.src_index for p in self.pages if p.separator}

    @property
    def split_starts(self) -> set[int]:
        """新文档起点页集合（拆分边界，页本身保留在组内）。"""
        return {p.src_index for p in self.pages if p.split_before}

    def snapshot(self) -> dict:
        return {
            "path": str(self.path),
            "pages": [p.snapshot() for p in self.pages],
            "groups": [g.snapshot() for g in self.groups],
            "status": self.status,
        }

    def restore(self, d: dict) -> None:
        self.pages = [PageState.restore(x) for x in d["pages"]]
        self.groups = [Group.restore(x) for x in d["groups"]]
        self.status = d["status"]


@dataclass
class Workspace:
    """所有已导入文档的工作区状态（撤销快照的最小单元）。"""

    documents: list[ScanDocument] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {"documents": [d.snapshot() for d in self.documents]}

    def restore(self, d: dict) -> None:
        by_path = {str(doc.path): doc for doc in self.documents}
        for dd in d["documents"]:
            doc = by_path.get(dd["path"])
            if doc:
                doc.restore(dd)
