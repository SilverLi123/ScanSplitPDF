"""Evidence gates and stage chaining; no new OCR model or export rules."""
import math
from . import autoname
from .ocr import OcrEngine
from .render import PageRenderer


def clearly_upright(result):
    """Certify maintaining 0 degrees only after complete geometric comparison.

    Few lines depress the legacy quality score. For this case require horizontal
    substantial text at 0, weak opposite direction and no horizontal side rival.
    This never authorizes applying a nonzero rotation.
    """
    q = result.qualities
    if result.suggested_angle != 0 or set(q) != {0, 90, 180, 270}:
        return False
    if any(not math.isfinite(item.get(k,0)) or item.get(k,0)<0
           for item in q.values() for k in ("quality","avg_score","horizontal_frac","substance_frac","n_lines")):
        return False
    current = q[0]
    if (current.get("n_lines", 0) < 3 or current.get("avg_score", 0) < .80
            or current.get("horizontal_frac", 0) < .95 or current.get("substance_frac", 0) < .75
            or current.get("quality", 0) <= 0):
        return False
    if q[180].get("quality", 0) > current["quality"] * .35:
        return False
    for a in (90, 270):
        rival = q[a]
        if rival.get("horizontal_frac", 0) > .20 or rival.get("quality", 0) > current["quality"]*.35:
            return False
    return True


def name_ready(doc, engine=None, progress=None, cancel=None):
    """Name only reliable groups. Partial attempts never mark the whole PDF done."""
    cancel = cancel or (lambda: False)
    if cancel() or not doc.analysis_complete or doc.analysis_stale:
        return []
    ready = {i for i, g in enumerate(doc.groups) if g.boundary_confirmed
             and not any(g.start <= p <= g.end+1 for p in doc.pending_boundaries)
             and not any(p.has_rotation_proposal for p in doc.pages[g.start-1:g.end])}
    if not ready:
        return []
    engine = engine or OcrEngine.get()
    if not engine.available:
        return []
    renderer = PageRenderer(doc.path)
    try:
        result = autoname.auto_name_groups(doc, renderer, engine=engine, only_indices=ready,
            preserve_confirmed=True, unit_mode="reporting", cancel_cb=cancel,
            progress_cb=(lambda i,n: progress(i,n,"提取标题与填报单位")) if progress else None)
        if not cancel():
            doc.naming_attempted = bool(doc.groups) and len(ready) == len(doc.groups)
        return result
    finally:
        renderer.close()
