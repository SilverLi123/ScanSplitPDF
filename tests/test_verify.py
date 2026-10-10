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


# -- v1.8：读取异常 fail-closed 与解码/原始字节区分 -------------------------------


def test_verify_mapping_fingerprint_exception_fails(tmp_path, make_scan, monkeypatch):
    """指纹计算抛异常 → 判失败（绝不静默放行）。"""
    pdf = make_scan("vf1.pdf", ["text"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    w.add_page(src.pages[0])
    out = tmp_path / "out.pdf"
    with open(out, "wb") as f:
        w.write(f)

    def boom(page):
        raise RuntimeError("模拟指纹计算崩溃")

    monkeypatch.setattr(verify, "page_fingerprint", boom)
    issues = verify.verify_mapping(pdf, out, [1])
    assert any("指纹计算失败" in i for i in issues)


def test_verify_mapping_read_errors_fail_closed(tmp_path, make_scan, monkeypatch):
    """内容流/图像资源读取异常 → fail-closed 判失败，不因双方都读不到而误判一致。"""
    pdf = make_scan("vf2.pdf", ["text"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    w.add_page(src.pages[0])
    out = tmp_path / "out.pdf"
    with open(out, "wb") as f:
        w.write(f)

    real_fp = verify.page_fingerprint

    def broken_read(page):
        fp = real_fp(page)
        fp["errors"] = ["模拟内容流读取异常"]
        return fp

    monkeypatch.setattr(verify, "page_fingerprint", broken_read)
    issues = verify.verify_mapping(pdf, out, [1])
    assert any("读取异常" in i for i in issues)


def test_fingerprint_includes_raw_and_errors(tmp_path, make_scan):
    """v1.8：指纹同时含解码哈希与原始编码流哈希；正常页面 errors 为空。"""
    pdf = make_scan("vf3.pdf", ["text", "table", "stamp"])
    src = PdfReader(str(pdf))
    for page in src.pages:
        fp = verify.page_fingerprint(page)
        assert fp["errors"] == []
        assert fp["contents_sha"] and fp["contents_raw_sha"]
        if fp["image_count"]:
            assert fp["image_raw_shas"] is not None
            assert len(fp["image_raw_shas"]) == fp["image_count"]


def test_verify_mapping_distinguishes_raw_reencode(tmp_path, make_scan, monkeypatch):
    """解码数据一致但原始编码流不同 → 报"原始编码流字节不一致"而非一致通过。"""
    pdf = make_scan("vf4.pdf", ["text"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    w.add_page(src.pages[0])
    out = tmp_path / "out.pdf"
    with open(out, "wb") as f:
        w.write(f)

    real_fp = verify.page_fingerprint

    # 让源页与输出页的原始流哈希不同（模拟重编码），解码哈希保持一致
    calls = {"n": 0}

    def alternating(page):
        fp = real_fp(page)
        calls["n"] += 1
        if calls["n"] % 2 == 0:  # 第二次调用是输出页
            fp["contents_raw_sha"] = "2" * 64
        return fp

    monkeypatch.setattr(verify, "page_fingerprint", alternating)
    issues = verify.verify_mapping(pdf, out, [1])
    assert any("原始编码流字节不一致" in i for i in issues)
