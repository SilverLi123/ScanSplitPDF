"""Bounded, cached margin OCR retries. Only analysis images are cropped."""
from dataclasses import replace
from . import pagenum, ocr_cache
from .ocr import OcrLine


WIDTH = 2400


def refine(renderer, index, engine, angle, base, cancel=lambda: False):
    """Use strong regional evidence; never choose between different numbers.

    Retry only non-reliable whole-page results. A conflicting or malformed
    whole-page candidate is never erased by a cleaner crop.
    """
    if base.reliable or not callable(getattr(engine, "recognize", None)) or cancel():
        return base
    if base.text and base.number is None:
        return base
    image = renderer.render(index, width_px=WIDTH, user_rotation=angle, bucket="pagination")
    w, h = image.size
    cache = ocr_cache.get_cache()
    lines = []
    for name, top, bottom in (("header", 0, int(h*.15)), ("footer", int(h*.85), h)):
        if cancel():
            return base
        key = ocr_cache.OcrCache.make_key(renderer, index, WIDTH, angle, "pagination-"+name+"-v1")
        found = cache.get(key) if cache.enabled else None
        if found is None:
            found = engine.recognize(image.crop((0, top, w, bottom)))
            if cache.enabled:
                cache.put(key, found)
        # Translate every OCR quadrilateral back into the full oriented image.
        lines.extend(OcrLine(l.text, l.score, [(x, y+top) for x, y in l.box]) for l in found)
    regional = pagenum.pagination_evidence(lines, w, h)
    if regional.text and regional.number is None:
        return replace(regional, reason="页边区域候选冲突或格式异常："+regional.reason)
    if base.number is not None and regional.number is not None:
        if base.number != regional.number or (base.total is not None and regional.total is not None and base.total != regional.total):
            return pagenum.PaginationEvidence(text=base.text+"；"+regional.text,
                reason="整页与页边区域的页码/总页数不同，请确认")
    if regional.reliable:
        return replace(regional, reason="高分辨率页边区域 OCR："+regional.reason)
    if not base.text and regional.text:
        return replace(regional, reason="页边区域 OCR 仍待确认："+regional.reason)
    return base
