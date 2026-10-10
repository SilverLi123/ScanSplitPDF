"""公章（红色印章）单位文字提取测试：弧形字展开、横排字、盖章压正文、
无印章不编造、红头不误判、与落款合并的优先级。

需要本地 OCR 引擎与中文字体；两者任一缺失则跳过。
"""

import pytest
from PIL import Image

from scansplit import ocrfields, ocrseal
from scansplit.ocr import OcrEngine, ocr_available
from tests import ocrimg

pytestmark = [
    pytest.mark.skipif(not ocr_available(), reason="未安装本地 OCR 组件"),
    pytest.mark.skipif(not ocrimg.fonts_available(), reason="系统无中文字体"),
]


def _engine():
    eng = OcrEngine.get()
    assert eng.available, f"OCR 引擎不可用：{eng.error}"
    return eng


def _page_with_seal(unit_arc="", unit_flat="", code="", size=(1400, 1800),
                    over_text: bool = False) -> Image.Image:
    img = Image.new("RGB", size, "white")
    seal = ocrimg.make_seal(unit_arc, unit_flat, code).convert("RGBA")
    pos = (img.width - seal.width - 60, img.height - seal.height - 120)
    if over_text:
        img = ocrimg.make_cn_document(
            "关于某事项的通知", ["各单位：", "现将有关事项通知如下。"], unit=None, size=size
        )
    img = img.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.paste(seal, pos, seal)
    return Image.alpha_composite(img, layer).convert("RGB")


def test_seal_arc_unit_extracted():
    img = _page_with_seal(unit_arc="华东师范大学教务处")
    cands = ocrseal.extract_seal_units(img, _engine())
    assert cands, "弧形印章文字应提取到候选"
    assert cands[0].text == "华东师范大学教务处"
    assert cands[0].source == "印章"


def test_seal_flat_unit_extracted():
    img = _page_with_seal(unit_flat="教务办公室", code="000123")
    cands = ocrseal.extract_seal_units(img, _engine())
    texts = [c.text for c in cands]
    assert "教务办公室" in texts
    assert all(c.source == "印章" for c in cands)
    assert not any("000123" in t for t in texts)  # 编号不得混入单位名


def test_seal_over_black_text_still_extracted():
    img = _page_with_seal(unit_arc="华东师范大学教务处", over_text=True)
    cands = ocrseal.extract_seal_units(img, _engine())
    assert cands, "盖章压在黑字上仍应识别出弧形文字"
    assert cands[0].text == "华东师范大学教务处"


def test_no_seal_no_fabrication():
    img = ocrimg.make_cn_document("关于某事项的通知", ["各单位："], unit=None)
    assert ocrseal.extract_seal_units(img, _engine()) == []


def test_red_header_not_mistaken_for_seal():
    """顶部横排红字（红头）不是圆环，不得产生印章候选。"""
    img = ocrimg.make_cn_document(
        "关于某事项的通知", ["各单位："], unit=None, header="华东师范大学文件"
    )
    assert ocrseal.extract_seal_units(img, _engine()) == []


def test_merge_prefers_luokuan_over_seal():
    """同文本同时来自落款与印章时，来源取落款；仅印章来源时才标印章。"""
    from scansplit.ocrfields import FieldCandidate

    luokuan = [FieldCandidate("教务处", 0.9, "落款", "位于日期上方")]
    seal = [FieldCandidate("教务处", 0.7, "印章", "印章弧形文字展开识别")]
    merged = ocrfields.merge_unit_candidates([], luokuan, seal)
    assert merged[0].source == "落款"
    assert merged[0].confidence == 0.9

    seal_only = ocrfields.merge_unit_candidates([], [], seal)
    assert seal_only[0].source == "印章"
