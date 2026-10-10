"""文件命名：`标题——单位.pdf` 模板、Windows 非法字符清理、防覆盖、单位历史、
以及可提取文字 PDF 的标题/单位候选提取（仅作建议，绝不伪造）。"""

from __future__ import annotations

import re
from pathlib import Path

ILLEGAL_CHARS = set('\\/:*?"<>|')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"}
RESERVED_NAMES |= {f"COM{i}" for i in range(1, 10)}
RESERVED_NAMES |= {f"LPT{i}" for i in range(1, 10)}
MAX_NAME_LEN = 180

DEFAULT_TEMPLATE = "{title}——{unit}"


def sanitize_filename(name: str) -> str:
    """清理 Windows 文件名非法字符，保留中文。返回清理后的字符串（可能为空）。"""
    if not name:
        return ""
    name = "".join(ch for ch in name if ch not in ILLEGAL_CHARS and ord(ch) >= 32)
    name = re.sub(r"[\t\r\n]+", " ", name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    name = name.rstrip(". ")
    if len(name) > MAX_NAME_LEN:
        name = name[:MAX_NAME_LEN].rstrip(". ")
    if not name:
        return ""
    stem = name.split(".")[0].upper()
    if stem in RESERVED_NAMES:
        name = "_" + name
    return name


def build_filename(
    title: str,
    unit: str,
    template: str = DEFAULT_TEMPLATE,
    index: int = 1,
    orig: str = "",
    start: int = 1,
    end: int = 1,
) -> str:
    """按模板生成文件名（不含 .pdf 扩展名）。{title}/{unit} 为空时用占位符，
    两者都为空时回退为 原文件名+页码。"""
    try:
        name = template.format(
            title=title.strip() or "未命名",
            unit=unit.strip() or "未知单位",
            index=index,
            orig=Path(orig).stem if orig else "",
            start=start,
            end=end,
        )
    except (KeyError, IndexError, ValueError):
        name = f"{title or '未命名'}——{unit or '未知单位'}"
    if not title.strip() and not unit.strip():
        base = Path(orig).stem if orig else f"文档{index}"
        name = f"{base}_第{start}-{end}页"
    return sanitize_filename(name) or f"文档{index}"


def unique_path(directory: str | Path, filename: str, ext: str = ".pdf") -> Path:
    """避免覆盖：重名时追加 (2)、(3)……"""
    directory = Path(directory)
    candidate = directory / (filename + ext)
    n = 2
    while candidate.exists():
        candidate = directory / f"{filename} ({n}){ext}"
        n += 1
    return candidate


# -- 单位历史 ---------------------------------------------------------------


class UnitHistory:
    def __init__(self, max_items: int = 30):
        self.max_items = max_items
        self._items: list[str] = []

    def add(self, unit: str) -> None:
        unit = unit.strip()
        if not unit:
            return
        if unit in self._items:
            self._items.remove(unit)
        self._items.insert(0, unit)
        del self._items[self.max_items :]

    @property
    def items(self) -> list[str]:
        return list(self._items)

    def to_json(self) -> list[str]:
        return self._items

    @staticmethod
    def from_json(data) -> "UnitHistory":
        h = UnitHistory()
        if isinstance(data, list):
            h._items = [str(x) for x in data[: h.max_items]]
        return h


# -- 可提取文字 PDF 的标题/单位建议 ------------------------------------------


def _collect_chunks(page) -> list[tuple[str, float, float, float]]:
    """返回 [(text, font_size, x, y)]，y 为 PDF 坐标（向上增大）。"""
    chunks: list[tuple[str, float, float, float]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text and text.strip():
            chunks.append((text, float(font_size or 0), float(tm[4]), float(tm[5])))

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        return []
    return chunks


def extract_title_candidates(reader, page_idx0: int) -> list[str]:
    """首页标题候选：页面上方区域中字号最大的文本（按字号降序、长度降序）。"""
    try:
        page = reader.pages[page_idx0]
        height = float(page.mediabox.height) if page.mediabox else 0.0
    except Exception:
        return []
    chunks = _collect_chunks(page)
    if not chunks or height <= 0:
        return []
    top = [c for c in chunks if c[3] >= height * 0.6] or chunks
    by_size: dict[float, str] = {}
    for text, size, x, y in top:
        size = round(size, 1)
        by_size.setdefault(size, "")
        by_size[size] += text
    out = []
    for size in sorted(by_size, reverse=True):
        merged = re.sub(r"\s+", " ", by_size[size]).strip()
        if merged and merged not in out:
            out.append(merged)
    return out[:5]


def extract_unit_candidates(reader, last_page_idx0: int) -> list[str]:
    """落款单位候选：最后一页末尾几行非空文本。"""
    try:
        page = reader.pages[last_page_idx0]
    except Exception:
        return []
    chunks = _collect_chunks(page)
    if not chunks:
        return []
    # 按 y 坐标分组为行，取最靠下的 3 行
    chunks.sort(key=lambda c: c[3])
    # 按 y 聚簇为行（容差 6pt），取最靠下的 3 行
    clusters: list[tuple[float, list[str]]] = []
    for text, size, x, y in chunks:
        if clusters and abs(y - clusters[-1][0]) <= 6:
            clusters[-1][1].append(text)
        else:
            clusters.append((y, [text]))
    out = []
    # 按 y 升序聚簇后，前 3 簇即页面最下方的 3 行（落款区）
    for _, parts in clusters[:3]:
        joined = "".join(parts)
        # pypdf 抽取 CJK 文本时常在字符间插入空格，仅对含汉字的行去空格
        line = re.sub(r"\s+", "", joined) if re.search(r"[一-鿿]", joined) else joined.strip()
        if line and line not in out and not re.fullmatch(r"[\d\-—._/\\ ]+", line):
            out.append(line)
    return out
