"""OCR 字段提取测试（字段级准确率）：标准公文、模糊、倾斜、大标题、
跨行标题、表格标题、正文多单位、收文单位排除、无单位不编造、方向对比。

需要本地 OCR 引擎与中文字体；两者任一缺失则跳过（记录于测试报告）。
"""

import pytest

from scansplit import ocrfields
from scansplit.ocr import OcrEngine, ocr_available
from tests import ocrimg


def _engine():
    return OcrEngine.get()


def _recognize(img):
    eng = _engine()
    assert eng.available, f"OCR 引擎不可用：{eng.error}"
    return eng.recognize(img)


def _top_title(cands):
    return cands[0].text if cands else ""


def _top_unit(cands):
    return cands[0].text if cands else ""


pytestmark = [
    pytest.mark.skipif(not ocr_available(), reason="未安装本地 OCR 组件"),
    pytest.mark.skipif(not ocrimg.fonts_available(), reason="系统无中文字体"),
]


def test_standard_notice_title_and_unit():
    img = ocrimg.make_cn_document(
        "关于开展教学检查的通知",
        ["各学院、各部门：", "为加强教学质量监控，经研究决定，", "本学期开展教学检查工作。"],
        unit="教务处",
    )
    lines = _recognize(img)
    w, h = img.size
    titles = ocrfields.extract_title(lines, w, h)
    units = ocrfields.extract_unit_last_page(lines, w, h)
    assert "教学检查" in _top_title(titles)
    assert _top_unit(units) == "教务处"


def test_multi_line_title_joined():
    img = ocrimg.make_cn_document(
        ["2026年度实验室", "安全检查报告"],
        ["为进一步加强实验室安全管理，", "现将有关工作安排如下。"],
        unit="计算机科学与技术学院",
    )
    lines = _recognize(img)
    w, h = img.size
    titles = ocrfields.extract_title(lines, w, h)
    assert "实验室" in _top_title(titles) or "检查报告" in _top_title(titles)


def test_large_title():
    img = ocrimg.make_cn_document(
        "重要通知",
        ["各有关单位：", "请按要求执行。"],
        unit="校长办公室",
        title_size=88,
    )
    lines = _recognize(img)
    w, h = img.size
    titles = ocrfields.extract_title(lines, w, h)
    assert "通知" in _top_title(titles)


def test_blurred_document():
    img = ocrimg.make_cn_document(
        "2026年度科研工作总结",
        ["一年来，科研工作取得显著进展，", "现将有关情况总结如下。"],
        unit="科研处",
        blur=1.5,
    )
    lines = _recognize(img)
    w, h = img.size
    titles = ocrfields.extract_title(lines, w, h)
    units = ocrfields.extract_unit_last_page(lines, w, h)
    assert "科研工作" in _top_title(titles) or "总结" in _top_title(titles)
    assert _top_unit(units) == "科研处"


def test_skewed_document():
    img = ocrimg.make_cn_document(
        "关于做好安全工作的通知",
        ["各单位：", "请认真落实各项安全措施。"],
        unit="保卫处",
        skew_deg=2.5,
    )
    lines = _recognize(img)
    w, h = img.size
    titles = ocrfields.extract_title(lines, w, h)
    units = ocrfields.extract_unit_last_page(lines, w, h)
    assert "安全" in _top_title(titles)
    assert _top_unit(units) == "保卫处"


def test_table_title():
    img = ocrimg.make_cn_document(
        "学生成绩统计表",
        body_lines=None,
        unit="教务处",
        landscape=True,
        table_grid=True,
    )
    lines = _recognize(img)
    w, h = img.size
    titles = ocrfields.extract_title(lines, w, h)
    assert "成绩" in _top_title(titles) or "统计表" in _top_title(titles)


def test_multiple_units_body_mentions():
    """正文提及多个单位：落款单位必须排第一，正文提及降权靠后。"""
    img = ocrimg.make_cn_document(
        "关于开展联合检查的通知",
        ["各学院、各部门：", "经教务处、学生处、后勤管理处协商，", "决定开展联合检查。"],
        unit="科研处",
    )
    lines = _recognize(img)
    w, h = img.size
    first = ocrfields.extract_unit_first_page(lines, w, h)
    last = ocrfields.extract_unit_last_page(lines, w, h)
    merged = ocrfields.merge_unit_candidates(first, last)
    assert _top_unit(merged) == "科研处"
    body_texts = "".join(c.text for c in merged)
    assert "教务处" in body_texts  # 正文提及仍作为候选供选择


def test_recipient_excluded():
    """收文单位（称呼行）不作为发文单位候选。"""
    img = ocrimg.make_cn_document(
        "关于做好开学准备工作的通知",
        ["各学院、各部门：", "现将开学准备工作通知如下。"],
        unit="学校办公室",
    )
    lines = _recognize(img)
    w, h = img.size
    units = ocrfields.extract_unit_last_page(lines, w, h)
    assert "各学院" not in _top_unit(units)


def test_no_unit_no_fabrication():
    """没有落款时返回空候选，绝不编造。"""
    img = ocrimg.make_cn_document(
        "会议记录",
        ["时间：2026年10月9日", "地点：会议室", "参加人员：全体成员"],
        unit=None,
    )
    lines = _recognize(img)
    w, h = img.size
    first = ocrfields.extract_unit_first_page(lines, w, h)
    last = ocrfields.extract_unit_last_page(lines, w, h)
    merged = ocrfields.merge_unit_candidates(first, last)
    assert merged == []


def test_labeled_unit_last_page():
    """末页"填报单位：教务处"标注行是明确落款，须压过底部表格单元格噪声。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1400, 1800), "white")
    d = ImageDraw.Draw(img)
    f = ocrimg._font(32)
    d.text((80, 1480), "填报单位：教务处", fill="black", font=f)
    d.text((80, 1600), "自动化学院", fill="black", font=f)  # 表格单元格噪声
    lines = _recognize(img)
    w, h = img.size
    units = ocrfields.extract_unit_last_page(lines, w, h)
    assert _top_unit(units) == "教务处"


def test_bottom_left_cell_not_issue_unit():
    """无日期锚定时，左下角单元格（非右下角落款）不得成为落款候选。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1400, 1800), "white")
    d = ImageDraw.Draw(img)
    f = ocrimg._font(32)
    d.text((80, 1600), "自动化学院", fill="black", font=f)
    lines = _recognize(img)
    w, h = img.size
    units = ocrfields.extract_unit_last_page(lines, w, h)
    assert units == []


def test_labeled_unit_first_page():
    """首页"部门：教务处"标注行作为文头候选。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1400, 1800), "white")
    d = ImageDraw.Draw(img)
    f = ocrimg._font(32)
    d.text((60, 100), "部门：教务处", fill="black", font=f)
    lines = _recognize(img)
    w, h = img.size
    units = ocrfields.extract_unit_first_page(lines, w, h)
    assert _top_unit(units) == "教务处"


def test_direction_quality_picks_upright():
    """同一张图旋转 90° 后，质量对比应选文字正立的方向。"""
    from PIL import Image

    base = ocrimg.make_cn_document(
        "年度报表",
        ["单位：万元"],
        unit="财务处",
        landscape=True,
        table_grid=True,
    )
    quals = {}
    for angle in (0, 90, 180, 270):
        img = base.rotate(angle, expand=True, fillcolor="white")
        lines = _recognize(img)
        quals[angle] = ocrfields.direction_quality({angle: lines})[angle]
    # 原图文字正立：0° 应显著优于 90°/270°
    assert quals[0]["n_lines"] >= max(quals[90]["n_lines"], quals[270]["n_lines"])
    assert quals[0]["avg_score"] > max(quals[90]["avg_score"], quals[270]["avg_score"])


def test_title_likeness_blank_vs_title():
    blank = ocrimg.make_cn_document("", body_lines=None)
    titled = ocrimg.make_cn_document("关于推进工作的通知", ["各单位："], unit="办公室")
    bl = _recognize(blank)
    tl = _recognize(titled)
    s_blank = ocrfields.title_likeness(bl, *blank.size)
    s_title = ocrfields.title_likeness(tl, *titled.size)
    assert s_title > 0.55
    assert s_blank < s_title
