"""自动命名流水线测试：OCR 自动命名、方向分析、边界双重验证、
以及"OCR 绝不修改源 PDF / 输出 PDF"的硬约束验证。"""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from scansplit import autoname, verify
from scansplit.autoname import analyze_directions, auto_name_groups, boundary_review
from scansplit.grouping import refresh_groups
from scansplit.loader import open_document
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine, ocr_available
from scansplit.render import PageRenderer
from tests import ocrimg
from tests.samplegen import _add_image_page


def _build_cn_scan(path: Path, images) -> Path:
    w = PdfWriter()
    for img in images:
        _add_image_page(w, img)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _blank_img(w=1240, h=1754):
    from PIL import Image

    return Image.new("RGB", (w, h), "white")


def _doc(path) -> ScanDocument:
    reader, warnings = open_document(path)
    return ScanDocument(path, reader, warnings)


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not ocr_available(), reason="未安装本地 OCR 组件"),
    pytest.mark.skipif(not ocrimg.fonts_available(), reason="系统无中文字体"),
]


@pytest.fixture(scope="module")
def named_scan(tmp_path_factory):
    """两份中文公文 + 空白分隔页的模拟扫描件（第 1 份两页：首页标题 + 末页落款）。"""
    tmp = tmp_path_factory.mktemp("autoname")
    p = tmp / "cn_scan.pdf"
    img1 = ocrimg.make_cn_document(
        "关于开展教学检查的通知",
        ["各学院、各部门：", "为加强教学质量监控，现将有关事项通知如下。"],
        unit=None,
    )
    img2 = ocrimg.make_cn_document(
        "",
        ["二、检查内容", "1. 课堂教学情况；", "2. 实验教学情况；", "三、有关要求", "请各单位认真落实。"],
        unit="教务处",
    )
    img3 = ocrimg.make_cn_document(
        ["2026年度实验室", "安全检查报告"],
        ["为进一步加强实验室安全管理，现将有关情况报告如下。"],
        unit="计算机科学与技术学院",
    )
    return _build_cn_scan(p, [img1, img2, _blank_img(), img3])


def test_auto_name_groups(named_scan):
    doc = _doc(named_scan)
    doc.pages[2].separator = True  # 空白页分隔
    refresh_groups(doc)
    assert [(g.start, g.end) for g in doc.groups] == [(1, 2), (4, 4)]
    renderer = PageRenderer(named_scan)
    try:
        results = auto_name_groups(doc, renderer, engine=OcrEngine.get())
        assert len(results) == 2
        g1, g2 = doc.groups
        assert "教学检查" in g1.title
        assert g1.unit == "教务处"
        assert "实验室" in g2.title and "报告" in g2.title
        assert g2.unit == "计算机科学与技术学院"
        assert not g1.need_confirm
    finally:
        renderer.close()


def test_auto_name_ocr_never_touches_pdf(named_scan):
    """OCR 分析前后：源文件哈希不变、页面指纹不变。"""
    before_hash = verify.file_sha256(named_scan)
    src = open_document(named_scan)[0]
    fps_before = [verify.page_fingerprint(p) for p in src.pages]

    doc = _doc(named_scan)
    doc.pages[2].separator = True
    refresh_groups(doc)
    renderer = PageRenderer(named_scan)
    try:
        auto_name_groups(doc, renderer, engine=OcrEngine.get())
    finally:
        renderer.close()

    assert verify.file_sha256(named_scan) == before_hash
    src2 = open_document(named_scan)[0]
    fps_after = [verify.page_fingerprint(p) for p in src2.pages]
    assert fps_before == fps_after


def test_analyze_directions_landscape_table(tmp_path):
    table_img = ocrimg.make_cn_document(
        "学生成绩统计表",
        body_lines=None,
        unit="教务处",
        landscape=True,
        table_grid=True,
    )
    p = _build_cn_scan(tmp_path / "t.pdf", [table_img])
    doc = _doc(p)
    renderer = PageRenderer(p)
    try:
        results = analyze_directions(doc, renderer, [1])
        assert len(results) == 1
        r = results[0]
        assert r.suggested_angle == 0  # 表格正立存储，0° 文字可读
    finally:
        renderer.close()


def test_analyze_directions_sideways_table(tmp_path):
    """横版表格被竖着扫描进纵向页（检测框竖行为主）：应建议旋转回正。

    内容顺时针侧放 → 导出需 /Rotate 270 才能回正。
    """
    from PIL import Image

    table_img = ocrimg.make_cn_document(
        "学生成绩统计表",
        body_lines=None,
        unit="教务处",
        landscape=True,
        table_grid=True,
    )
    sideways = table_img.rotate(-90, expand=True)  # 顺时针侧放（PIL 逆时针为正）
    canvas = Image.new("RGB", (1240, 1754), "white")
    canvas.paste(sideways, ((1240 - sideways.width) // 2, (1754 - sideways.height) // 2))
    p = _build_cn_scan(tmp_path / "s.pdf", [canvas])
    doc = _doc(p)
    renderer = PageRenderer(p)
    try:
        results = analyze_directions(doc, renderer, [1])
        assert len(results) == 1
        r = results[0]
        assert r.suggested_angle == 270
        # 正确方向质量显著高于反方向
        assert r.qualities[270]["quality"] > r.qualities[90]["quality"]
    finally:
        renderer.close()


def test_analyze_directions_upside_down(tmp_path):
    """180° 倒置扫描：cls 引擎四方向都能读通（旧逻辑会误判），新逻辑应建议 180。"""
    from PIL import Image

    img = ocrimg.make_cn_document("关于开展检查的通知", ["各单位："], unit="办公室")
    flipped = img.rotate(180)
    p = _build_cn_scan(tmp_path / "u.pdf", [flipped])
    doc = _doc(p)
    renderer = PageRenderer(p)
    try:
        results = analyze_directions(doc, renderer, [1])
        assert len(results) == 1
        r = results[0]
        assert r.suggested_angle == 180
        assert r.qualities[180]["quality"] > r.qualities[0]["quality"]
    finally:
        renderer.close()


def test_auto_name_sideways_first_page(tmp_path):
    """侧放首页的命名提取：标题/单位应按回正后的内容识别，而非竖排垃圾。"""
    from PIL import Image

    title_img = ocrimg.make_cn_document(
        "关于开展实验室安全检查的通知",
        ["各学院：", "现将有关事项通知如下。"],
        unit=None,
    )
    sideways = title_img.rotate(-90, expand=True)
    canvas = Image.new("RGB", (1240, 1754), "white")
    canvas.paste(sideways, ((1240 - sideways.width) // 2, (1754 - sideways.height) // 2))
    p = _build_cn_scan(tmp_path / "n.pdf", [canvas])
    doc = _doc(p)
    refresh_groups(doc)
    renderer = PageRenderer(p)
    try:
        results = auto_name_groups(doc, renderer, engine=OcrEngine.get())
        assert len(results) == 1
        g = doc.groups[0]
        assert "实验室安全检查" in g.title
    finally:
        renderer.close()


def test_boundary_review_ok_and_warn(tmp_path):
    img_ok = ocrimg.make_cn_document("关于开展检查的通知", ["各单位："], unit="办公室")
    img_blank = _blank_img()
    img_cont = ocrimg.make_cn_document("", ["二、检查情况说明。", "1. 检查时间：2026年10月。", "2. 检查结果：合格。"], unit=None)
    p = _build_cn_scan(tmp_path / "b.pdf", [img_ok, img_blank, img_ok, img_blank, img_cont])
    doc = _doc(p)
    doc.pages[1].separator = True
    doc.pages[3].separator = True
    refresh_groups(doc)
    renderer = PageRenderer(p)
    try:
        statuses = boundary_review(doc, renderer, OcrEngine.get(), scan_internal=False)
    finally:
        renderer.close()
    by_page = {s.next_page: s for s in statuses}
    assert by_page[3].status == "ok"  # 第 3 页有标题特征 → 拆分可信
    assert by_page[5].status == "warn"  # 第 5 页无标题特征 → 警告人工确认


def test_no_ocr_engine_text_layer_fallback(tmp_path, make_text):
    """无 OCR 环境（engine=None）时走文本层提取路径。"""
    pdf = make_text(
        "text_only.pdf",
        [
            [
                ("2026 ANNUAL REPORT", 24.0, 150, 760),
                ("body line", 11.0, 80, 400),
            ],
            [("more body", 11.0, 80, 400), ("Registrar Office", 11.0, 80, 120)],
        ],
    )
    doc = _doc(pdf)
    doc.groups = [doc.groups[0] if doc.groups else None] or None
    from scansplit.model import Group

    doc.groups = [Group(start=1, end=2)]
    renderer = PageRenderer(pdf)
    try:
        results = auto_name_groups(doc, renderer, engine=None)
    finally:
        renderer.close()
    assert len(results) == 1
    assert "2026 ANNUAL REPORT" in doc.groups[0].title
    assert "Registrar Office" in doc.groups[0].unit
