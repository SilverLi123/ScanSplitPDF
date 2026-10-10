"""端到端测试：模拟两份中文公文的扫描件 → 检测 → 分组 → OCR 命名 → 旋转 → 无损导出，
并对输出 PDF 做保真校验（内容流+图像哈希+页面映射）与源文件完整性复检。"""

from pathlib import Path

import pytest
from pypdf import PdfReader

from scansplit import exporter, grouping, verify
from scansplit.autoname import auto_name_groups, boundary_review
from scansplit.blankdetect import DetectParams, detect_document
from scansplit.loader import open_document
from scansplit.model import ScanDocument
from scansplit.naming import DEFAULT_TEMPLATE
from scansplit.ocr import OcrEngine, ocr_available
from scansplit.render import PageRenderer
from tests import ocrimg
from tests.samplegen import _add_image_page


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not ocr_available(), reason="未安装本地 OCR 组件"),
    pytest.mark.skipif(not ocrimg.fonts_available(), reason="系统无中文字体"),
]


def _build_scan(path: Path, images) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    for img in images:
        _add_image_page(w, img)
    with open(path, "wb") as f:
        w.write(f)
    return path


@pytest.fixture(scope="module")
def e2e_scan(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    p = tmp / "e2e_scan.pdf"
    doc1_p1 = ocrimg.make_cn_document(
        "关于开展教学检查的通知",
        ["各学院、各部门：", "为加强教学质量监控，现将有关事项通知如下。"],
        unit=None,
    )
    doc1_p2 = ocrimg.make_cn_document(
        "",
        ["二、检查内容", "1. 课堂教学情况；", "三、有关要求", "请各单位认真落实。"],
        unit="教务处",
    )
    blank = ocrimg.make_cn_document("", body_lines=None)
    doc2_p1 = ocrimg.make_cn_document(
        ["2026年度实验室", "安全检查报告"],
        ["为进一步加强实验室安全管理，现将有关情况报告如下。"],
        unit=None,
    )
    doc2_p2 = ocrimg.make_cn_document(
        "检查情况统计表",
        body_lines=None,
        unit="计算机科学与技术学院",
        landscape=True,
        table_grid=True,
    )
    return _build_scan(p, [doc1_p1, doc1_p2, blank, doc2_p1, doc2_p2])


def test_end_to_end_pipeline(e2e_scan, tmp_path):
    src_hash_before = verify.file_sha256(e2e_scan)
    reader, warnings = open_document(e2e_scan)
    doc = ScanDocument(e2e_scan, reader, warnings)
    renderer = PageRenderer(e2e_scan)
    out_dir = tmp_path / "out"
    try:
        # 1) 空白检测
        results = detect_document(
            renderer, doc.page_count, DetectParams(), lambda i: False
        )
        assert len(results) == 5
        assert results[2].verdict == "blank"
        for page, res in zip(doc.pages, results):
            page.blank = res

        # 2) 自动分组：[(1,2), (4,5)]
        grouping.auto_group(doc)
        assert [(g.start, g.end) for g in doc.groups] == [(1, 2), (4, 5)]

        # 3) OCR 自动命名
        auto_name_groups(doc, renderer, engine=OcrEngine.get())
        g1, g2 = doc.groups
        assert "教学检查" in g1.title
        assert g1.unit == "教务处"
        assert "实验室" in g2.title and ("报告" in g2.title or "检查" in g2.title)
        assert g2.unit == "计算机科学与技术学院"

        # 4) 边界复核：第 4 页（新文档首页）应具备标题特征
        statuses = boundary_review(doc, renderer, OcrEngine.get(), scan_internal=False)
        by_next = {s.next_page: s for s in statuses}
        assert by_next[4].status == "ok"

        # 5) 旋转：横版表格页顺时针 90°（模拟"侧躺"页面被用户确认纠正）
        table_page = doc.pages[4]
        table_page.rotation = 90

        # 6) 导出
        opts = exporter.ExportOptions(template=DEFAULT_TEMPLATE, out_dir=out_dir)
        issues = exporter.preflight([doc], opts)
        assert issues == []
        report = exporter.export_documents([doc], opts)
        assert len(report.succeeded) == 2 and not report.failed

        # 7) 文件名：标题——单位.pdf
        names = {r.path.name for r in report.succeeded}
        assert any("教学检查" in n and "教务处" in n for n in names)
        assert any("实验室" in n and "计算机科学与技术学院" in n for n in names)

        # 8) 输出页码与旋转核对
        out_pages = []
        for r in report.succeeded:
            rdr = PdfReader(str(r.path))
            out_pages += list(rdr.pages)
        assert len(out_pages) == 4
        assert int(out_pages[3].get("/Rotate", 0)) == 90

        # 9) 保真校验：每份输出的内容流与嵌入图像与源页一致
        for r in report.succeeded:
            page_map = list(range(r.group.start, r.group.end + 1))
            assert verify.verify_mapping(e2e_scan, r.path, page_map) == []

        # 10) 源文件完整性
        assert report.src_sha_before[str(e2e_scan)] == report.src_sha_after[str(e2e_scan)]
        assert verify.file_sha256(e2e_scan) == src_hash_before
    finally:
        renderer.close()
