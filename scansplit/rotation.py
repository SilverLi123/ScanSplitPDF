"""旋转：目标角度存储、/Rotate 写入、横竖版方向建议。

保真约束：旋转仅通过 PDF 页面 /Rotate 属性实现，绝不重绘页面内容。
"""

from __future__ import annotations

from pypdf import PdfReader
from pypdf.generic import NameObject, NumberObject

from .model import ScanDocument

VALID_ANGLES = (0, 90, 180, 270)


def normalize(angle: int) -> int:
    return int(angle) % 360


def stored_rotation(page) -> int:
    """页面原有的 /Rotate 值。"""
    try:
        return int(page.get("/Rotate", 0) or 0) % 360
    except Exception:
        return 0


def apply_target_rotation(page, target: int) -> None:
    """把页面 /Rotate 直接设为目标角度（绝对值，避免累加）。"""
    target = normalize(target)
    if target == 0:
        try:
            page.pop(NameObject("/Rotate"), None)
        except Exception:
            pass
    else:
        page[NameObject("/Rotate")] = NumberObject(target)


def rotate_pages(doc: ScanDocument, pages_1based: list[int], angle: int) -> None:
    """按绝对目标角度设置一组页面的旋转。"""
    angle = normalize(angle)
    idx = set(pages_1based)
    for p in doc.pages:
        if p.src_index in idx:
            p.rotation = angle


def display_is_landscape(page) -> bool:
    """考虑原 /Rotate 后的显示方向是否为横向。"""
    try:
        mb = page.mediabox
        w = float(mb.width)
        h = float(mb.height)
    except Exception:
        return False
    if stored_rotation(page) % 180 == 90:
        w, h = h, w
    return w > h


def orientation_suggestions(doc: ScanDocument) -> dict[int, str]:
    """找出与文档主流方向不同的页面（疑似需旋转）。

    仅提示"方向不一致"，不猜测 90°/270° 的旋转方向——该判断必须由用户确认。
    """
    n_portrait = sum(1 for p in doc.pages if not _page_landscape(doc, p.src_index))
    majority_portrait = n_portrait >= len(doc.pages) / 2
    out: dict[int, str] = {}
    for p in doc.pages:
        if p.separator:
            continue  # 分隔页不进入导出，无需旋转提示
        landscape = _page_landscape(doc, p.src_index)
        if landscape != (not majority_portrait):
            out[p.src_index] = "横向" if landscape else "纵向"
    return out


def _page_landscape(doc: ScanDocument, src_index: int) -> bool:
    try:
        return display_is_landscape(doc.reader.pages[src_index - 1])
    except Exception:
        return False
