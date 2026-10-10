"""旋转测试：/Rotate 写入、绝对角度设置、方向建议、连续多页旋转导出。"""

from pypdf import PdfReader
from pypdf.generic import NameObject

from scansplit import rotation
from scansplit.exporter import ExportOptions, export_documents
from scansplit.grouping import refresh_groups
from scansplit.loader import open_document
from scansplit.model import Group, ScanDocument


def test_normalize():
    assert rotation.normalize(90) == 90
    assert rotation.normalize(450) == 90
    assert rotation.normalize(-90) == 270


def test_apply_target_rotation_absolute():
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    page = w.pages[0]
    rotation.apply_target_rotation(page, 90)
    assert int(page.get("/Rotate", 0)) == 90
    rotation.apply_target_rotation(page, 270)
    assert int(page.get("/Rotate", 0)) == 270
    rotation.apply_target_rotation(page, 0)
    assert page.get("/Rotate", None) in (None, 0)
    rotation.apply_target_rotation(page, 90)
    rotation.apply_target_rotation(page, 90)  # 幂等：重复设置不叠加
    assert int(page.get("/Rotate", 0)) == 90


def test_rotate_pages_multi_and_range(make_scan):
    pdf = make_scan("r.pdf", ["text", "text", "text", "text", "text"])
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    rotation.rotate_pages(doc, [2, 3, 4], 90)
    assert [p.rotation for p in doc.pages] == [0, 90, 90, 90, 0]
    rotation.rotate_pages(doc, list(range(1, 6)), 270)  # 整份文档
    assert [p.rotation for p in doc.pages] == [270] * 5
    rotation.rotate_pages(doc, [3], 180)
    assert doc.pages[2].rotation == 180


def test_orientation_suggestions_landscape_pages(make_scan):
    """连续 5 页横向表格 + 多数竖版：横向页被标记为待确认。"""
    kinds = ["text", "text", "text", "table", "table", "table", "table", "text", "text"]
    pdf = make_scan("mix.pdf", kinds)
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    sug = rotation.orientation_suggestions(doc)
    assert set(sug.keys()) == {4, 5, 6, 7}
    assert all(v == "横向" for v in sug.values())


def test_orientation_suggestions_skips_separators(make_scan):
    """横向分隔页不进入导出，方向建议应跳过它。"""
    kinds = ["text", "table", "text"]
    pdf = make_scan("sep.pdf", kinds)
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    doc.pages[1].separator = True  # 第 2 页横向、是分隔页
    sug = rotation.orientation_suggestions(doc)
    assert sug == {}


def test_orientation_suggestions_majority_landscape(make_scan):
    kinds = ["table", "table", "table", "text"]
    pdf = make_scan("mix2.pdf", kinds)
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    sug = rotation.orientation_suggestions(doc)
    assert set(sug.keys()) == {4}
    assert sug[4] == "纵向"


def test_export_applies_rotation(tmp_path, make_scan):
    """连续 5 页横向表格旋转 90° 后导出：/Rotate 写入、内容流不变。"""
    pdf = make_scan("rot.pdf", ["table"] * 5)
    reader, warnings = open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    rotation.rotate_pages(doc, list(range(1, 6)), 90)
    doc.groups = [Group(start=1, end=5, title="表格", unit="测试处")]
    out_dir = tmp_path / "out"
    report = export_documents([doc], ExportOptions(out_dir=out_dir))
    assert len(report.succeeded) == 1
    out = PdfReader(str(report.succeeded[0].path))
    assert len(out.pages) == 5
    for p in out.pages:
        assert int(p.get("/Rotate", 0)) == 90
    # 内容流与源一致（旋转仅体现在 /Rotate）
    src = PdfReader(str(pdf))
    for i in range(5):
        from scansplit.verify import _contents_bytes

        assert _contents_bytes(src.pages[i]) == _contents_bytes(out.pages[i])
