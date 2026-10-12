"""Output-only removal of verified blank pages, independent of document boundaries."""
import math
import numpy as np
from . import blankdetect
from .model import BLANK


def valid_exclusion(page):
    e = page.blank_exclusion_evidence
    s = e.get("signals", {})
    bounds = dict(ink_ratio=.005, dark_ratio=.02, grad_ratio=.35, dark_run_frac=.01)
    return bool(page.omitted_blank and page.blank and page.blank.verdict == BLANK
        and e.get("method") == "full-page-2400-v1" and e.get("text_layer_empty") is True
        and e.get("verdict") == BLANK
        and isinstance(e.get("confidence"), (int, float)) and math.isfinite(e["confidence"])
        and .98 <= e["confidence"] <= 1
        and all(isinstance(s.get(k), (int, float)) and math.isfinite(s[k]) and 0 <= s[k] <= v
                for k, v in bounds.items()))


def apply(doc, renderer, cancel=lambda: False, progress=lambda *a: None):
    """Check uncropped high-resolution image and text layer before any omission.

    A failed check keeps the page. Manual keep/remove decisions always win.
    Cropped detection can miss marginal text, so promote it back to content/maybe.
    """
    params = blankdetect.DetectParams(margin_lr=0, margin_tb=0)
    for i, page in enumerate(doc.pages):
        if cancel():
            return
        if page.src_index in doc.manual_separator_pages:
            continue
        page.omitted_blank = False
        page.blank_exclusion_evidence = {}
        if not page.blank or page.blank.verdict != BLANK:
            continue
        progress(i+1, doc.page_count, "复核空白页（含页边内容）")
        try:
            text = (doc.reader.pages[i].extract_text() or "").strip()
            image = renderer.render(i, width_px=2400, bucket="blank-policy")
            checked = blankdetect.detect_page(np.asarray(image.convert("L")), params, has_text=bool(text))
            page.blank_exclusion_evidence = dict(method="full-page-2400-v1",
                text_layer_empty=not bool(text), verdict=checked.verdict,
                confidence=checked.confidence, signals=dict(checked.signals))
            if checked.verdict != BLANK:
                page.blank = checked
            else:
                page.omitted_blank = True
                if not valid_exclusion(page):
                    page.omitted_blank = False
        except Exception:
            # No confirmed exclusion evidence on render/text-layer failure.
            page.omitted_blank = False
            page.blank_exclusion_evidence = {}
