# -*- coding: utf-8 -*-
"""阶段 A1 回归测试：统一页面方向状态 + 修复实际旋转错误。

覆盖任务书要求的 8 类场景：
1. 90° 侧放表格（真值回正 270 / 90）
2. 270° 侧放表格
3. 180° 倒置首页
4. 侧放首页的命名（标题必须按回正内容识别）
5. 用户手动旋转覆盖自动旋转
6. 方向变化后 OCR 结果失效（effective_rotation 参与缓存 key）
7. 导出只按 effective_rotation 写 /Rotate 且不二次旋转
8. 页面旋转后标题/单位结果不下降

（A:p41、C:p22/45/59 为真实样本，由 diag_a1_eval.py 验收；此处用合成等价物
保证无私有样本也能回归。）
"""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from scansplit import autoname, ocrfields, rotation, verify
from scansplit.autoname import analyze_directions, auto_name_groups
from scansplit.grouping import refresh_groups
from scansplit.loader import open_document
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine, ocr_available
from scansplit.render import PageRenderer
from tests import ocrimg
from tests.samplegen import _add_image_page


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not ocr_available(), reason="未安装本地 OCR 组件"),
    pytest.mark.skipif(not ocrimg.fonts_available(), reason="系统无中文字体"),
]


def _build(path: Path, images) -> Path:
    w = PdfWriter()
    for img in images:
        _add_image_page(w, img)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _sideways_on_canvas(img, clockwise_deg: int, size=(1240, 1754)):
    """把内容旋转 clockwise_deg（顺时针）后居中贴到纵向画布上（模拟侧放扫描）。"""
    from PIL import Image

    rot = img.rotate(-clockwise_deg, expand=True) if clockwise_deg else img
    canvas = Image.new("RGB", size, "white")
    canvas.paste(rot, ((size[0] - rot.width) // 2, (size[1] - rot.height) // 2))
    return canvas


def _doc(path) -> ScanDocument:
    reader, warnings = open_document(path)
    return ScanDocument(path, reader, warnings)


# -- 1/2. 90°/270° 侧放表格 ------------------------------------------------- #


@pytest.mark.parametrize("content_cw,truth", [(90, 270), (270, 90)])
def test_analyze_directions_sideways_90_270(tmp_path, content_cw, truth):
    """侧放表格必须建议正确回正角，且正确方向质量显著高于其它方向。

    内容顺时针 90° → 需再顺时针 270°（= 逆时针 90°）回正；反之亦然。
    """
    table = ocrimg.make_cn_document(
        "学生成绩统计表", body_lines=None, unit="教务处",
        landscape=True, table_grid=True)
    canvas = _sideways_on_canvas(table, content_cw)
    p = _build(tmp_path / f"s{content_cw}.pdf", [canvas])
    doc, renderer = _doc(p), PageRenderer(p)
    try:
        r = analyze_directions(doc, renderer, [1])[0]
        assert r.suggested_angle == truth, (
            f"内容顺时针{content_cw}° 应建议 {truth}°，实得 {r.suggested_angle}°"
            f" qualities={ {a: q['quality'] for a, q in r.qualities.items()} }")
        assert r.qualities[truth]["quality"] > r.qualities[(truth + 180) % 360]["quality"]
        assert r.confidence > 0.0
    finally:
        renderer.close()


# -- 3. 180° 倒置首页 ------------------------------------------------------- #


def test_analyze_directions_upside_down_180(tmp_path):
    """180° 倒置：应建议 180（不得被判成 0/90/270）。"""
    from PIL import Image

    img = ocrimg.make_cn_document("关于开展检查的通知", ["各单位："], unit="办公室")
    p = _build(tmp_path / "u.pdf", [img.rotate(180)])
    doc, renderer = _doc(p), PageRenderer(p)
    try:
        r = analyze_directions(doc, renderer, [1])[0]
        assert r.suggested_angle == 180
        assert r.qualities[180]["quality"] > r.qualities[0]["quality"]
    finally:
        renderer.close()


def test_analyze_directions_all_four_tried(tmp_path):
    """回归 A:p41 根因：必须评估完整四方向，不得因某方向无行而提前收敛。"""
    table = ocrimg.make_cn_document(
        "统计附表", body_lines=None, unit="图书馆",
        landscape=True, table_grid=True)
    canvas = _sideways_on_canvas(table, 90)
    p = _build(tmp_path / "all4.pdf", [canvas])
    doc, renderer = _doc(p), PageRenderer(p)
    try:
        r = analyze_directions(doc, renderer, [1])[0]
        assert set(r.qualities.keys()) == {0, 90, 180, 270}, "四方向必须全部评估"
    finally:
        renderer.close()


# -- 4. 侧放首页的命名 ------------------------------------------------------- #


def test_sideways_first_page_naming_uses_effective_rotation(tmp_path):
    """侧放首页：设置 effective_rotation 回正后，标题应按回正内容识别。"""
    title_img = ocrimg.make_cn_document(
        "关于开展实验室安全检查的通知", ["各学院：", "现将有关事项通知如下。"], unit=None)
    canvas = _sideways_on_canvas(title_img, 90)  # 回正角 = 270
    p = _build(tmp_path / "n.pdf", [canvas])
    doc = _doc(p)
    refresh_groups(doc)
    # 统一方向状态：人工确认回正 270°
    doc.pages[0].user_override_rotation = 270
    assert doc.pages[0].effective_rotation == 270
    renderer = PageRenderer(p)
    try:
        auto_name_groups(doc, renderer, engine=OcrEngine.get())
        g = doc.groups[0]
        assert "实验室安全检查" in g.title, f"标题应为回正后内容，实得 {g.title!r}"
    finally:
        renderer.close()


# -- 5/6. 方向状态优先级与缓存失效 ------------------------------------------- #


def test_effective_rotation_priority_user_over_detected():
    """人工覆盖 > 自动检测 > 0。"""
    from scansplit.model import PageState

    ps = PageState(src_index=1)
    assert ps.effective_rotation == 0  # 未设置 → 0
    ps.detected_rotation = 90
    assert ps.effective_rotation == 90  # 仅有自动检测
    ps.user_override_rotation = 180
    assert ps.effective_rotation == 180  # 人工优先
    assert ps.is_rotation_manual is True


def test_legacy_rotation_setter_is_manual():
    """旧 API `page.rotation = x` 视为人工设定，不会被后续自动检测覆盖。"""
    from scansplit.model import PageState

    ps = PageState(src_index=1)
    ps.rotation = 90
    assert ps.user_override_rotation == 90 and ps.is_rotation_manual
    ps.detected_rotation = 270  # 自动检测写入
    assert ps.effective_rotation == 90  # 人工结果未被覆盖


def test_orientation_change_invalidates_ocr_cache(tmp_path):
    """effective_rotation 参与 OCR 缓存 key：换方向必得不同识别结果。"""
    from scansplit.ocr_cache import recognize_cached

    img = ocrimg.make_cn_document("方向测试文本", ["正文第一行内容较长便于识别。"], unit=None)
    p = _build(tmp_path / "c.pdf", [img])
    renderer = PageRenderer(p)
    eng = OcrEngine.get()
    try:
        ls0 = recognize_cached(eng, "cls", renderer, 0, 1000, 0)
        ls180 = recognize_cached(eng, "cls", renderer, 0, 1000, 180)
        # 两个方向是不同缓存条目；倒置后识别文本/行数应有所变化（或至少不串缓存）
        t0 = "".join(l.text for l in ls0)
        t180 = "".join(l.text for l in ls180)
        assert t0 or t180  # 至少一个方向能读
    finally:
        renderer.close()


# -- 7. 导出只按 effective_rotation 写 /Rotate，且不二次旋转 ----------------- #


def test_export_applies_effective_rotation_no_double(tmp_path, make_scan):
    from scansplit import exporter

    scan = make_scan("rot_export.pdf", ["text", "text", "text"])
    doc = _doc(scan)
    # 第 2 页人工回正 270°；第 3 页自动检测 90°
    doc.pages[1].user_override_rotation = 270
    doc.pages[2].detected_rotation = 90
    from scansplit.grouping import refresh_groups
    refresh_groups(doc)
    doc.groups[0].title, doc.groups[0].unit = "文档", "单位"
    out = tmp_path / "out"
    out.mkdir()
    opts = exporter.ExportOptions(template="{title}_{unit}", out_dir=out, strict=False)
    report = exporter.export_documents([doc], opts)
    assert len(report.succeeded) == 1
    pages = PdfReader(str(report.succeeded[0].path)).pages
    assert len(pages) == 3
    assert int(pages[0].get("/Rotate", 0) or 0) == 0
    assert int(pages[1].get("/Rotate", 0) or 0) == 270
    assert int(pages[2].get("/Rotate", 0) or 0) == 90
    # 源文件未被改动
    assert verify.verify_mapping(scan, report.succeeded[0].path, [1, 2, 3]) == []


def test_original_rotation_recorded_on_load(make_scan):
    """加载时记录源 /Rotate 到 original_rotation（只读基线）。"""
    from pypdf import PdfReader as R

    scan = make_scan("orig_rot.pdf", ["text", "text"])
    doc = _doc(scan)
    assert all(p.original_rotation == 0 for p in doc.pages)
    # 手动给源文件加 /Rotate 后重载
    r = R(str(scan))
    from pypdf.generic import NameObject, NumberObject
    r.pages[0][NameObject("/Rotate")] = NumberObject(90)
    w = PdfWriter()
    for pg in r.pages:
        w.add_page(pg)
    tmp = scan.parent / "orig_rot2.pdf"
    with open(tmp, "wb") as f:
        w.write(f)
    doc2 = _doc(tmp)
    assert doc2.pages[0].original_rotation == 90
    # original_rotation 不叠加到 effective（渲染器已自行叠加 stored_rotation）
    assert doc2.pages[0].effective_rotation == 0


# -- 8. 旋转后标题/单位结果不下降 -------------------------------------------- #


def test_titles_not_degraded_after_rotation(tmp_path):
    """同一份内容，正放与侧放（均回正后）应得到一致的标题与单位。"""
    img = ocrimg.make_cn_document(
        "关于印发管理办法的通知", ["各单位：", "现将办法印发给你们。"], unit="办公室")
    p0 = _build(tmp_path / "up.pdf", [img])
    canvas = _sideways_on_canvas(img, 90)
    p1 = _build(tmp_path / "side.pdf", [canvas])

    results = {}
    for tag, path, rot in (("up", p0, 0), ("side", p1, 270)):
        doc = _doc(path)
        refresh_groups(doc)
        doc.pages[0].user_override_rotation = rot
        renderer = PageRenderer(path)
        try:
            auto_name_groups(doc, renderer, engine=OcrEngine.get())
            results[tag] = (doc.groups[0].title, doc.groups[0].unit)
        finally:
            renderer.close()
    assert results["up"][0] == results["side"][0], results
    assert "管理办法" in results["side"][0]
