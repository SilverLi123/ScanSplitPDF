"""命名测试：中文保留、非法字符清理、防覆盖、模板、文本层标题/单位提取。"""

from pypdf import PdfReader

from scansplit import naming
from scansplit.naming import (
    DEFAULT_TEMPLATE,
    UnitHistory,
    build_filename,
    extract_title_candidates,
    extract_unit_candidates,
    sanitize_filename,
    unique_path,
)


def test_chinese_preserved():
    assert sanitize_filename("实验室安全检查报告——计算机科学与技术学院") == (
        "实验室安全检查报告——计算机科学与技术学院"
    )


def test_illegal_chars_removed():
    s = 'a/b\\c:d*e?f"g<h>i|j'
    assert sanitize_filename(s) == "abcdefghij"


def test_trailing_dots_and_spaces():
    assert sanitize_filename("  报告.  ") == "报告"
    assert sanitize_filename("报告...") == "报告"


def test_reserved_names():
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("com1.pdf") == "_com1.pdf"


def test_empty_and_whitespace():
    assert sanitize_filename("") == ""
    assert sanitize_filename("   ") == ""


def test_long_name_truncated():
    long = "文" * 300
    out = sanitize_filename(long)
    assert len(out) <= naming.MAX_NAME_LEN


def test_build_filename_default_template():
    assert build_filename("年度报告", "教务处") == "年度报告——教务处"


def test_build_filename_custom_template():
    name = build_filename("报告", "教务处", template="{unit}_{title}")
    assert name == "教务处_报告"


def test_build_filename_empty_fields_fallback():
    name = build_filename("", "", orig="扫描件.pdf", start=3, end=5)
    assert name == "扫描件_第3-5页"


def test_unique_path_avoids_overwrite(tmp_path):
    p1 = unique_path(tmp_path, "报告——教务处")
    p1.write_bytes(b"x")
    p2 = unique_path(tmp_path, "报告——教务处")
    assert p2.name == "报告——教务处 (2).pdf"
    p2.write_bytes(b"x")
    p3 = unique_path(tmp_path, "报告——教务处")
    assert p3.name == "报告——教务处 (3).pdf"


def test_unit_history():
    h = UnitHistory()
    h.add("教务处")
    h.add("科研处")
    h.add("教务处")
    assert h.items == ["教务处", "科研处"]
    h2 = UnitHistory.from_json(h.to_json())
    assert h2.items == h.items


def test_extract_title_from_text_pdf(make_text):
    pdf = make_text(
        "t.pdf",
        [
            [
                ("2026 LAB REPORT", 24.0, 150, 760),
                ("some body text line one", 11.0, 80, 400),
                ("some body text line two", 11.0, 80, 380),
            ],
            [
                ("more body text", 11.0, 80, 400),
                ("Academic Affairs Office", 11.0, 80, 120),
            ],
        ],
    )
    reader = PdfReader(str(pdf))
    titles = extract_title_candidates(reader, 0)
    assert "2026 LAB REPORT" in titles[0]
    units = extract_unit_candidates(reader, 1)
    assert any("Academic Affairs Office" in u for u in units)


def test_extract_title_scan_pdf_no_fabrication(make_scan):
    """纯扫描 PDF 无文本层：返回空候选，绝不伪造。"""
    pdf = make_scan("scan.pdf", ["text"])
    reader = PdfReader(str(pdf))
    assert extract_title_candidates(reader, 0) == []
    assert extract_unit_candidates(reader, 0) == []
