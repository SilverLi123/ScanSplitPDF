"""空白页检测测试：覆盖任务书第 1~4 类场景（纯白/阴影/噪点/边缘黑线/背面/浅色印章）。"""

import numpy as np

from scansplit import blankdetect
from scansplit.blankdetect import DetectParams, detect_page
from scansplit.model import BLANK, CONTENT, MAYBE
from tests.samplegen import make_page


def _gray(kind: str, **kw) -> np.ndarray:
    return np.asarray(make_page(kind, **kw).convert("L"))


def test_pure_white_is_blank():
    r = detect_page(_gray("blank"), DetectParams())
    assert r.verdict == BLANK
    assert r.confidence > 0.9
    assert r.signals["ink_ratio"] < 0.05


def test_shade_page_is_blank():
    r = detect_page(_gray("shade"), DetectParams())
    assert r.verdict == BLANK


def test_noise_page_is_blank():
    r = detect_page(_gray("noise"), DetectParams())
    assert r.verdict == BLANK


def test_edge_lines_page_is_blank():
    r = detect_page(_gray("edge_lines"), DetectParams())
    assert r.verdict == BLANK
    assert r.signals["edge_ink"] > 10  # 边缘墨迹确实被检测到，但被裁剪排除


def test_backside_page_is_blank():
    r = detect_page(_gray("backside"), DetectParams())
    assert r.verdict == BLANK


def test_stamp_page_never_blank():
    """含浅色印章/签名的页面绝不判空白（保守）。"""
    r = detect_page(_gray("stamp"), DetectParams())
    assert r.verdict != BLANK
    assert r.verdict in (MAYBE, CONTENT)


def test_text_page_is_content():
    r = detect_page(_gray("text"), DetectParams())
    assert r.verdict == CONTENT


def test_table_page_is_content():
    r = detect_page(_gray("table"), DetectParams())
    assert r.verdict == CONTENT


def test_faint_text_never_blank():
    """浅色（浅灰）文字页面绝不判空白。"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (1200, 1600), 255)
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=42)
    y = 200
    for _ in range(12):
        d.text((100, y), "faint content line here", fill=205, font=font)
        y += 100
    r = detect_page(np.asarray(img), DetectParams())
    assert r.verdict != BLANK


def test_dark_page_never_blank():
    """整体很暗的页面（照片/黑底）保守判内容。"""
    dark = np.full((500, 500), 60, dtype=np.uint8)
    r = detect_page(dark, DetectParams())
    assert r.verdict == CONTENT


def test_text_layer_wins_over_image():
    """有实质文本层的页面即使图像很干净也判内容。"""
    r = detect_page(_gray("blank"), DetectParams(), has_text=True)
    assert r.verdict == CONTENT
    assert r.confidence == 1.0


def test_threshold_adjustment():
    """阈值可调：放宽后原判"疑似"的页可判空白。"""
    img = _gray("stamp")
    strict = DetectParams(ink_threshold_pct=0.1, maybe_ink_max_pct=0.5)
    r_strict = detect_page(img, strict)
    assert r_strict.verdict == CONTENT  # 严阈值下印章页绝不空白
    loose = DetectParams(ink_threshold_pct=3.0, maybe_ink_max_pct=6.0, dark_pct_blank=0.5)
    r_loose = detect_page(img, loose)
    assert r_loose.verdict != BLANK  # 即使放宽，深色印章仍不判空白（双重保险）


def test_confidence_monotonic():
    """墨迹越少，空白置信度越高。"""
    r1 = detect_page(_gray("blank"), DetectParams())
    r2 = detect_page(_gray("shade"), DetectParams())
    assert r1.confidence >= r2.confidence


def test_signals_reported():
    r = detect_page(_gray("blank"), DetectParams())
    for key in ("ink_ratio", "dark_ratio", "bbox_frac", "edge_ink", "mean"):
        assert key in r.signals
