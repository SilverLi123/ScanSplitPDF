"""印刷页码提取与页码重置信号。

适用场景：无空白页分隔的连续扫描件。从整页 OCR 行中识别页面印刷页码
（页脚区域优先，兼容页眉页码），页码出现"重置"（小于前一有页码页）时
给出"新文档起点"候选信号。

该信号只作拆分建议之一，必须与标题特征双重验证
（见 autoname.smart_split_review）：双信号一致才自动补拆，
单信号仅提示人工确认——绝不凭单一信号自动改动分组。

页码行用整行模式匹配（纯数字 / 第X页 / X/Y / —X— / Page X），
避免把落款日期"2026年10月9日"里的数字误当页码。
OCR 只读渲染图像，识别内容绝不写回 PDF。
"""

from __future__ import annotations

import re

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
