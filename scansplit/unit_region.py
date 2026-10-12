"""Read the last retained page's bottom-right region; never certify an issuer."""
import re
import math

from . import ocr_cache, ocrfields
from .ocr import OcrLine

WIDTH = 2400
SOURCE = "末页右下角落款"
_SENTENCE = re.compile(r"^(?:经(?!济|贸)|由|请|会同|有关|各单位|各部门)|负责|报送|抄送|主送|联系|使用|感谢|完善|提交|送交")
_NAME = re.compile(r"^[\u4e00-\u9fffA-Za-z（）()·]+$")
_OTHER_ROLE = re.compile(r"承办|使用单位|所在单位|收文|主送|抄送|联系|经办|主管")


def extract(lines, width, height, page):
    """Explicit reporting labels take precedence; signatures remain suggestions.

    Coordinates are those of the entire already oriented image. Restrict generic
    names to standalone organisation-shaped lines in the lower right. A nearby
    different role label must not be reinterpreted as a reporting unit.
    """
    result = ocrfields.extract_reporting_unit(lines, width, height)
    explicit = {c.text for c in result}
    for c in result:
        c.reason = "末页右下角高清区域 OCR；明确的填报单位标签"
        c.page = page
    for line in lines:
        text = line.text.strip()
        if (text in explicit or not math.isfinite(line.score) or not .6 <= line.score <= 1
                or line.cx < width * .6
                or line.cy < height * .7 or line.width <= line.height
                or not ocrfields.UNIT_PATTERN.fullmatch(text)
                or not _NAME.fullmatch(text) or _SENTENCE.search(text)
                or text in ocrfields.PLACE_STOPWORDS):
            continue
        if any(_OTHER_ROLE.search(other.text) and
               abs(other.cy-line.cy) <= max(other.height, line.height)*1.5
               for other in lines if other is not line):
            continue
        result.append(ocrfields.FieldCandidate(text, round(line.score * .9, 3), SOURCE,
            "末页右下角高清区域 OCR；落款候选，尚未确认是否为填报单位",
            page=page, bbox=[line.x0, line.y0, line.x1, line.y1],
            image_size=[width, height]))
    return result


def scan(renderer, index, engine, page_state, cancel=lambda: False):
    """Crop only the analysis image; preserve rotation and original PDF bytes."""
    if cancel():
        return []
    angle = page_state.effective_rotation
    image = renderer.render(index, width_px=WIDTH, user_rotation=angle, bucket="unit-region")
    w, h = image.size
    left, top = int(w * .35), int(h * .65)
    cache = ocr_cache.get_cache()
    key = ocr_cache.OcrCache.make_key(renderer, index, WIDTH, angle, "unit-bottom-right-v1")
    found = cache.get(key) if cache.enabled else None
    if found is None:
        if cancel():
            return []
        found = engine.recognize(image.crop((left, top, w, h)))
        if cache.enabled:
            cache.put(key, found)
    lines = [OcrLine(l.text, l.score, [(x+left, y+top) for x, y in l.box]) for l in found]
    return extract(lines, w, h, index+1)


def merge(primary, regional):
    """Keep all different values visible. Same text retains strongest provenance."""
    merged = {}
    def rank(c):
        return (c.source == "填报单位", c.confidence)
    for c in [*primary, *regional]:
        if c.text not in merged or rank(c) > rank(merged[c.text]):
            merged[c.text] = c
    return sorted(merged.values(), key=rank, reverse=True)
