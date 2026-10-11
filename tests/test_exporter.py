"""导出测试：拆分正确性、源文件哈希不变、内容流/图像保真、防覆盖、
取消导出、失败处理、100+ 页性能。"""

import pytest
from pypdf import PdfReader

from scansplit import verify
from scansplit.exporter import (
    ExportOptions,
    ExportResult,
    coverage_issues,
    export_documents,
    preflight,
)
from scansplit.grouping import refresh_groups
from scansplit.loader import open_document
from scansplit.model import Group, ScanDocument


def _load_doc(pdf) -> ScanDocument:
    reader, warnings = open_document(pdf)
    return ScanDocument(pdf, reader, warnings)


def test_split_export_basic(tmp_path, make_scan):
    pdf = make_scan("e.pdf", ["text", "text", "blank", "text", "blank", "text"])
    doc = _load_doc(pdf)
    doc.pages[2].separator = True
    doc.pages[4].separator = True
    doc.manual_separator_pages.update({3, 5})
    refresh_groups(doc)
    doc.groups[0].title, doc.groups[0].unit = "文档A", "教务处"
    doc.groups[1].title, doc.groups[1].unit = "文档B", "科研处"
    doc.groups[2].title, doc.groups[2].unit = "文档C", "学生处"
    out_dir = tmp_path / "out"
    report = export_documents([doc], ExportOptions(out_dir=out_dir))
    assert len(report.succeeded) == 3
    names = sorted(p.name for p in (r.path for r in report.succeeded))
    assert names == ["文档A——教务处.pdf", "文档B——科研处.pdf", "文档C——学生处.pdf"]
    for r, expected in zip(report.succeeded, (2, 1, 1)):
        assert len(PdfReader(str(r.path)).pages) == expected
    # 无漏页/重复页
    all_pages = []
    for r in report.succeeded:
        all_pages.extend(r.group.pages)
    assert sorted(all_pages) == [1, 2, 4, 6]
    assert len(all_pages) == len(set(all_pages))


def test_source_file_sha256_unchanged(tmp_path, make_scan):
    pdf = make_scan("s.pdf", ["text", "blank", "text"])
    before = verify.file_sha256(pdf)
    doc = _load_doc(pdf)
    doc.pages[1].separator = True
    doc.manual_separator_pages.add(2)
    refresh_groups(doc)
    doc.groups[0].title, doc.groups[0].unit = "A", "B"
    doc.groups[1].title, doc.groups[1].unit = "C", "D"
    report = export_documents([doc], ExportOptions(out_dir=tmp_path / "out"))
    assert report.src_sha_before[str(pdf)] == report.src_sha_after[str(pdf)]
    assert verify.file_sha256(pdf) == before


def test_output_page_fidelity(tmp_path, make_scan):
    """输出页面内容流与嵌入图像与源完全一致。"""
    pdf = make_scan("f.pdf", ["text", "table", "blank", "stamp"])
    doc = _load_doc(pdf)
    doc.pages[2].separator = True
    doc.manual_separator_pages.add(3)
    refresh_groups(doc)
    doc.groups[0].title, doc.groups[0].unit = "A", "B"
    doc.groups[1].title, doc.groups[1].unit = "C", "D"
    report = export_documents([doc], ExportOptions(out_dir=tmp_path / "out"))
    src = PdfReader(str(pdf))
    for r in report.succeeded:
        out = PdfReader(str(r.path))
        for out_idx, src_page in enumerate(r.group.pages):
            f1 = verify.page_fingerprint(src.pages[src_page - 1])
            f2 = verify.page_fingerprint(out.pages[out_idx])
            assert f1["contents_sha"] == f2["contents_sha"]
            assert f1["image_shas"] == f2["image_shas"]
            assert f1["image_count"] == f2["image_count"] == 1


def test_duplicate_names_get_suffix(tmp_path, make_scan):
    pdf1 = make_scan("a.pdf", ["text", "blank", "text"])
    pdf2 = make_scan("b.pdf", ["text", "blank", "text"])
    docs = []
    for pdf in (pdf1, pdf2):
        doc = _load_doc(pdf)
        doc.pages[1].separator = True
        doc.manual_separator_pages.add(2)
        refresh_groups(doc)
        for g in doc.groups:
            g.title, g.unit = "同名报告", "教务处"
        docs.append(doc)
    report = export_documents(docs, ExportOptions(out_dir=tmp_path / "out"))
    assert len(report.succeeded) == 4
    names = sorted(r.path.name for r in report.succeeded)
    assert names == ["同名报告——教务处 (2).pdf", "同名报告——教务处 (3).pdf",
                     "同名报告——教务处 (4).pdf", "同名报告——教务处.pdf"]


def test_cancel_export_keeps_partial_results(tmp_path, make_scan):
    pdf = make_scan("c.pdf", ["text", "blank", "text", "blank", "text", "blank", "text"])
    doc = _load_doc(pdf)
    for i in (1, 3, 5):
        doc.pages[i].separator = True
        doc.manual_separator_pages.add(i + 1)
    refresh_groups(doc)
    for g in doc.groups:
        g.title, g.unit = "报告", "单位"
    calls = {"n": 0}

    def cancel_after_two():
        calls["n"] += 1
        return calls["n"] > 2

    opts = ExportOptions(out_dir=tmp_path / "out", cancel_check=cancel_after_two)
    report = export_documents([doc], opts)
    assert report.canceled
    assert 1 <= len(report.succeeded) < 4  # 已导出的有效结果保留
    for r in report.succeeded:
        assert r.path.exists()


def test_export_failure_keeps_source_and_valid_results(tmp_path, make_scan, monkeypatch):
    pdf = make_scan("fail.pdf", ["text", "blank", "text", "blank", "text"])
    doc = _load_doc(pdf)
    for i in (1, 3):
        doc.pages[i].separator = True
        doc.manual_separator_pages.add(i + 1)
    refresh_groups(doc)
    for g in doc.groups:
        g.title, g.unit = "报告", "单位"

    import scansplit.exporter as exporter

    real_unique = exporter.unique_path

    def broken_unique(directory, filename, ext=".pdf"):
        if filename == "报告——单位" and not hasattr(broken_unique, "done"):
            broken_unique.done = True
            raise OSError("模拟磁盘错误")
        return real_unique(directory, filename, ext)

    monkeypatch.setattr(exporter, "unique_path", broken_unique)
    report = export_documents([doc], ExportOptions(out_dir=tmp_path / "out"))
    assert len(report.failed) == 1
    assert "模拟磁盘错误" in report.failed[0].error
    assert len(report.succeeded) == 2
    assert verify.file_sha256(pdf) == report.src_sha_after[str(pdf)]


def test_preflight_blocks_empty_names(tmp_path, make_scan):
    pdf = make_scan("p.pdf", ["text", "blank", "text"])
    doc = _load_doc(pdf)
    doc.pages[1].separator = True
    refresh_groups(doc)
    issues = preflight([doc], ExportOptions(out_dir=tmp_path / "out"))
    assert any("标题为空" in i for i in issues)
    doc.groups[0].title, doc.groups[0].unit = "A", "B"
    issues = preflight([doc], ExportOptions(out_dir=tmp_path / "out"))
    assert any("单位为空白" in i for i in issues)


def test_preflight_bad_output_dir(make_scan):
    pdf = make_scan("pd.pdf", ["text"])
    doc = _load_doc(pdf)
    doc.groups = [Group(start=1, end=1, title="A", unit="B")]
    issues = preflight([doc], ExportOptions(out_dir="Z:\\绝对\\不存在\\的\\路径\\x\\y"))
    assert any("无法创建输出目录" in i for i in issues)


def test_corrupt_pdf_raises(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(Exception):
        open_document(bad)


def test_encrypted_pdf_refused(tmp_path, make_scan):
    from pypdf import PdfWriter

    pdf = make_scan("enc.pdf", ["text"])
    src = PdfReader(str(pdf))
    w = PdfWriter()
    for p in src.pages:
        w.add_page(p)
    w.encrypt("secret123")
    enc_path = tmp_path / "encrypted.pdf"
    with open(enc_path, "wb") as f:
        w.write(f)
    with pytest.raises(Exception):
        open_document(enc_path)


# -- v1.8 严格导出模式 ----------------------------------------------------------


def test_strict_preflight_blocks_unconfirmed_fields(tmp_path, make_scan):
    """待确认字段不可被旧 strict=False 参数绕过。"""
    pdf = make_scan("st.pdf", ["text", "blank", "text"])
    doc = _load_doc(pdf)
    doc.pages[1].separator = True
    refresh_groups(doc)
    for g in doc.groups:
        g.title, g.unit = "标题", "单位"
        g.title_confirmed = False  # 模拟 OCR 低置信
    strict_issues = preflight([doc], ExportOptions(out_dir=tmp_path / "out", strict=True))
    assert any("待确认" in i for i in strict_issues)
    soft_issues = preflight([doc], ExportOptions(out_dir=tmp_path / "out", strict=False))
    assert any("待确认" in i for i in soft_issues)
    # 空标题在两种模式下都拦截
    doc.groups[0].title = ""
    issues = preflight([doc], ExportOptions(out_dir=tmp_path / "out", strict=False))
    assert any("标题为空" in i for i in issues)


def test_strict_preflight_all_confirmed_passes(tmp_path, make_scan):
    pdf = make_scan("st2.pdf", ["text"])
    doc = _load_doc(pdf)
    doc.groups = [Group(start=1, end=1, title="A", unit="B")]
    assert preflight([doc], ExportOptions(out_dir=tmp_path / "out", strict=True)) == []


# -- v1.8 全局页面覆盖率校验 -----------------------------------------------------


def test_coverage_detects_missing_and_duplicate_pages(make_scan):
    pdf = make_scan("cov.pdf", ["text"] * 6)
    doc = _load_doc(pdf)
    doc.groups = [Group(start=1, end=3, title="A", unit="B"), Group(start=5, end=6, title="C", unit="D")]
    # 只导出了第一组 → 第 5、6 页漏导
    results = [ExportResult(group=doc.groups[0], doc_name=doc.name, ok=True, pages=[1, 2, 3])]
    issues = coverage_issues(doc, results)
    assert any("未导出" in i and "5" in i for i in issues)
    # 重复导出同一组
    results2 = [
        ExportResult(group=doc.groups[0], doc_name=doc.name, ok=True, pages=[1, 2, 3]),
        ExportResult(group=doc.groups[0], doc_name=doc.name, ok=True, pages=[1, 2, 3]),
    ]
    issues2 = coverage_issues(doc, results2)
    assert any("重复" in i for i in issues2)
    assert any("未导出" in i and "5" in i for i in issues2)


def test_internal_blank_page_not_dropped(tmp_path, make_scan):
    """v1.8 回归：文档内部空白页（未标记分隔页）必须随组导出，不得被误删。"""
    pdf = make_scan("ib.pdf", ["text", "blank", "text"])
    doc = _load_doc(pdf)
    refresh_groups(doc)  # 无分隔页 → 单组 (1,3)，含内部空白页
    assert [(g.start, g.end) for g in doc.groups] == [(1, 3)]
    doc.groups[0].title, doc.groups[0].unit = "含空白页文档", "测试单位"
    report = export_documents([doc], ExportOptions(out_dir=tmp_path / "out"))
    assert len(report.succeeded) == 1
    r = report.succeeded[0]
    assert len(PdfReader(str(r.path)).pages) == 3  # 空白页在输出里
    assert not report.coverage_issues  # 覆盖率校验通过：3 页恰好各导出一次
    # 空白页内容与源一致（未被替换/丢失）
    src = PdfReader(str(pdf))
    out = PdfReader(str(r.path))
    assert verify.page_fingerprint(src.pages[1])["contents_sha"] == \
        verify.page_fingerprint(out.pages[1])["contents_sha"]


def test_export_report_coverage_logged(tmp_path, make_scan, capsys):
    """正常导出时日志含覆盖率校验通过说明。"""
    pdf = make_scan("cl.pdf", ["text", "blank", "text"])
    doc = _load_doc(pdf)
    doc.pages[1].separator = True
    doc.pages[1].blank_context = "separator"  # v1.9 A0：经确认的分隔页（业务属性）
    refresh_groups(doc)
    for g in doc.groups:
        g.title, g.unit = f"T{g.start}", "U"
    report = export_documents([doc], ExportOptions(out_dir=tmp_path / "out"))
    assert not report.coverage_issues
    assert any("覆盖率校验通过" in line for line in report.logs)


@pytest.mark.slow
def test_large_pdf_120_pages(tmp_path, make_scan):
    """100 页以上 PDF：检测 + 拆分 + 导出 + 保真校验。"""
    kinds = ["text", "text", "blank"] * 38 + ["text", "table", "table", "blank", "text", "text"]
    pdf = make_scan("big.pdf", kinds)
    assert len(PdfReader(str(pdf)).pages) >= 100
    doc = _load_doc(pdf)

    from scansplit.blankdetect import DetectParams, detect_document
    from scansplit.render import PageRenderer

    renderer = PageRenderer(pdf)
    params = DetectParams()

    def has_text(i):
        return False

    results = detect_document(renderer, doc.page_count, params, has_text)
    assert len(results) == doc.page_count
    for p, r in zip(doc.pages, results):
        p.blank = r
        if r.verdict == "blank":
            p.separator = True
            doc.manual_separator_pages.add(p.src_index)
    refresh_groups(doc)
    assert len(doc.groups) >= 38
    for gi, g in enumerate(doc.groups):
        g.title, g.unit = f"报告{gi + 1}", "测试单位"
    report = export_documents([doc], ExportOptions(out_dir=tmp_path / "out"))
    assert len(report.succeeded) == len(doc.groups)
    assert not report.failed
    renderer.close()
