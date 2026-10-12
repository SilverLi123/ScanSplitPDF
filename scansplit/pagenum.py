"""印刷页码提取与页码重置信号。

适用场景：无空白页分隔的连续扫描件。从整页 OCR 行中识别页面印刷页码
（页脚区域优先，兼容页眉页码），页码出现"重置"（小于前一有页码页）时
给出"新文档起点"候选信号。

旧 extract_page_number/reset_points 接口供历史补拆建议使用。
统一自动分析使用 pagination_evidence：严格检查区域、格式、识别分数及
候选冲突，交给 jointsplit 比较相邻内容页；可靠重启无需空白或标题变化，
续表、附件和总页数等矛盾证据仍必须人工确认。

页码行用整行模式匹配（纯数字 / 第X页 / X/Y / —X— / Page X），
避免把落款日期"2026年10月9日"里的数字误当页码。
OCR 只读渲染图像，识别内容绝不写回 PDF。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class PaginationEvidence:
    number: int | None = None
    total: int | None = None
    reliable: bool = False
    text: str = ""
    reason: str = "未找到可靠印刷页码"

    def to_dict(self):
        return asdict(self)


def pagination_evidence(lines, page_w: float, page_h: float) -> PaginationEvidence:
    """Strict evidence for automatic pagination splits; legacy API unchanged.

    Explicit labels in margins qualify. Bare numbers additionally need an extreme,
    centred margin position. Conflicting candidates are never resolved by guessing.
    OCR scores are evidence thresholds, not calibrated correctness probabilities.
    """
    found = []
    for line in lines:
        cy = (line.y0 + line.y1) / 2
        if not (cy <= page_h * .15 or cy >= page_h * .85):
            continue
        text = unicodedata.normalize("NFKC", line.text).strip()
        explicit = re.fullmatch(r"第\s*(\d{1,4})\s*页(?:\s*[,，]?\s*共\s*(\d{1,4})\s*页)?", text)
        if explicit is None:
            explicit = re.fullmatch(r"page\s*(\d{1,4})(?:\s*(?:of|/)\s*(\d{1,4}))?", text, re.I)
        match = explicit or re.fullmatch(r"(\d{1,4})\s*/\s*(\d{1,4})", text)
        if match:
            number = int(match.group(1))
            total = int(match.group(2)) if match.group(2) else None
        else:
            number, total = _match_page_number(text), None
        if number is None:
            continue
        if number < 1 or (total is not None and not number <= total <= _MAX_PAGENO):
            found.append(PaginationEvidence(text=text, reason="页码或总页数格式不合理"))
            continue
        score = float(getattr(line, "score", 0) or 0)
        centred = (hasattr(line, "x0") and hasattr(line, "x1") and page_w > 0
                   and abs((line.x0 + line.x1)/2 - page_w/2) <= page_w * .18)
        extreme = cy <= page_h * .06 or cy >= page_h * .94
        reliable = score >= .85 if explicit else score >= .90 and centred and extreme and not 1900 <= number <= 2099
        found.append(PaginationEvidence(number, total, reliable, text,
            "明确页码标签" if explicit and reliable else "页边居中印刷页码" if reliable else "数字位置或识别证据不足"))
    if not found:
        return PaginationEvidence()
    if len({(c.number, c.total) for c in found}) != 1:
        return PaginationEvidence(text="；".join(c.text for c in found), reason="页眉页脚存在不同页码候选")
    # Every matching line must qualify; duplicate OCR lines do not add certainty.
    best = found[0]
    if not all(c.reliable for c in found):
        return PaginationEvidence(best.number, best.total, False, best.text, "页码识别证据不足或重复候选不一致")
    return best

# 整行匹配的页码文本模式（按优先级排列）
_PATTERNS = [
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^[-—–·•]?\s*\d{1,4}\s*[-—–·•]?$"),
    re.compile(r"^第\s*(\d{1,4})\s*页(\s*[,，]?\s*共\s*\d{1,4}\s*页)?$"),
    re.compile(r"^(\d{1,4})\s*/\s*\d{1,4}$"),
    re.compile(r"^[Pp]age\s*(\d{1,4})(\s*(?:of|/)\s*\d{1,4})?$"),
]

_MAX_PAGENO = 9999  # 超过视为误识（如把年份 2026 当页码）


def _match_page_number(text: str) -> int | None:
    t = text.strip()
    if not t or len(t) > 20:
        return None
    for pat in _PATTERNS:
        m = pat.match(t)
        if not m:
            continue
        d = re.search(r"\d{1,4}", t)
        if d:
            n = int(d.group())
            if 1 <= n <= _MAX_PAGENO:
                return n
    return None


def extract_page_number(lines, page_h: float) -> int | None:
    """从 OCR 行中提取印刷页码。

    页脚区域（cy ≥ 0.85×页高）优先，取区域内最靠下的一行；
    页脚没有时退回页眉区域（cy ≤ 0.15×页高）。
    找不到返回 None（绝不猜测）。
    """
    def scan(lo: float, hi: float) -> int | None:
        best: tuple[float, int] | None = None
        for ln in lines:
            cy = (ln.y0 + ln.y1) / 2.0
            if not (lo <= cy <= hi):
                continue
            n = _match_page_number(ln.text)
            if n is None:
                continue
            if best is None or cy > best[0]:
                best = (cy, n)
        return best[1] if best else None

    n = scan(0.85 * page_h, page_h)
    if n is not None:
        return n
    return scan(0.0, 0.15 * page_h)


def reset_points(numbers: dict[int, int | None]) -> list[int]:
    """页码重置点：某页页码小于前一有页码页的页码 → 新文档起点候选。

    numbers: {页号(1-based): 页码或 None}；None 页跳过（不重置比较）。
    """
    out: list[int] = []
    prev: int | None = None
    for p in sorted(numbers):
        n = numbers[p]
        if n is None:
            continue
        if prev is not None and n < prev:
            out.append(p)
        prev = n
    return out
