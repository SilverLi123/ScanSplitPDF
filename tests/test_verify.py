"""保真校验测试：哈希、指纹、映射校验的正常与异常路径。"""

import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from scansplit import verify
from scansplit.loader import open_document


def test_file_sha256_stable(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world" * 1000)
    h1 = verify.file_sha256(f)
    assert verify.file_sha256(f) == h1
    f.write_bytes(b"changed")
    assert verify.file_sha256(f) != h1


def test_page_fingerprint_identical_after_copy(tmp_path, make_scan):
    pdf = make_scan("v.pdf", ["text", "table"])
    src = PdfReader(str(pdf))
    f1 = verify.page_fingerprint(src.pages[0])

    w = PdfWriter()
    w.add_page(src.pages[0])
    buf = io.BytesIO()
    w.write(buf)
    buf.seek(0)
    f2 = verify.page_fingerprint(PdfReader(buf).pages[0])
    assert f1 == f2


def test_page_fingerprint_detects_content_change(tmp_path, make_scan):
    pdf = make_scan("v2.pdf", ["text"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    w.add_page(src.pages[0])
    # 追加一段内容流，模拟内容被改写
    cs = DecodedStreamObject()
    cs.set_data(b"q Q 1 0 0 1 0 0 cm")
    w.pages[0][NameObject("/Contents")] = w._add_object(cs)
    buf = io.BytesIO()
    w.write(buf)
    buf.seek(0)
    f1 = verify.page_fingerprint(src.pages[0])
    f2 = verify.page_fingerprint(PdfReader(buf).pages[0])
    assert f1["contents_sha"] != f2["contents_sha"]


def test_verify_mapping_pass(tmp_path, make_scan):
    pdf = make_scan("vm.pdf", ["text", "text", "text"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    for i in (0, 2):
        w.add_page(src.pages[i])
    out = tmp_path / "out.pdf"
    with open(out, "wb") as f:
        w.write(f)
    assert verify.verify_mapping(pdf, out, [1, 3]) == []


def test_verify_mapping_catches_wrong_page(tmp_path, make_scan):
    pdf = make_scan("vm2.pdf", ["text", "table"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    w.add_page(src.pages[1])
    out = tmp_path / "out.pdf"
    with open(out, "wb") as f:
        w.write(f)
    issues = verify.verify_mapping(pdf, out, [1])  # 声称是第 1 页，实际是第 2 页
    assert any("不一致" in i for i in issues)


def test_verify_mapping_catches_page_count_mismatch(tmp_path, make_scan):
    pdf = make_scan("vm3.pdf", ["text", "text"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    w.add_page(src.pages[0])
    out = tmp_path / "out.pdf"
    with open(out, "wb") as f:
        w.write(f)
    issues = verify.verify_mapping(pdf, out, [1, 2])
    assert any("页数" in i for i in issues)


def test_verify_mapping_missing_files(tmp_path):
    assert verify.verify_mapping(tmp_path / "none.pdf", tmp_path / "none2.pdf", [1])
