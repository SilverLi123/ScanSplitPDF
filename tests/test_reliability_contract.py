import pytest
from pypdf import PdfReader, PdfWriter

from scansplit import exporter, grouping, autoname, ocrfields
from scansplit.model import ScanDocument, Group
from scansplit.ocr import OcrLine


def load(path):
    return ScanDocument(path, PdfReader(str(path)))


@pytest.mark.parametrize('partial', [False, True])
def test_direct_export_blocks_unexplained_exclusion(tmp_path, make_scan, partial):
    doc = load(make_scan('unsafe.pdf', ['text', 'blank', 'text']))
    doc.pages[1].separator = True
    grouping.refresh_groups(doc)
    for g in doc.groups:
        g.title, g.unit = 'A', 'B'
    report = exporter.export_documents([doc], exporter.ExportOptions(
        out_dir=tmp_path / 'out', strict=False, only={(0, 0)} if partial else None))
    assert not report.succeeded
    assert report.coverage_issues
    assert not list((tmp_path / 'out').glob('*.pdf'))


def test_direct_export_blocks_overlap(tmp_path, make_scan):
    doc = load(make_scan('overlap.pdf', ['text'] * 3))
    doc.groups = [Group(1, 2, 'A', 'B'), Group(2, 3, 'C', 'D')]
    report = exporter.export_documents([doc], exporter.ExportOptions(out_dir=tmp_path / 'out'))
    assert not report.succeeded
    assert report.coverage_issues


def test_source_changed_since_import_blocks_export(tmp_path, make_scan):
    path = make_scan('changed.pdf', ['text'])
    doc = load(path)
    doc.groups = [Group(1, 1, 'A', 'B')]
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(path, 'wb') as stream:
        writer.write(stream)
    report = exporter.export_documents([doc], exporter.ExportOptions(out_dir=tmp_path / 'out'))
    assert not report.succeeded
    assert report.coverage_issues


def line(text, x, y, w=240, h=30, score=.98):
    return OcrLine(text, score, [[x,y],[x+w,y],[x+w,y+h],[x,y+h]])


def test_reporting_unit_beats_issuer_and_signature():
    lines = [line('某大学文件', 50, 20), line('填报单位：教务处', 80, 200),
             line('科研处', 650, 850), line('2026年10月11日', 650, 900)]
    candidates = ocrfields.extract_reporting_unit(lines, 1000, 1400)
    assert [c.text for c in candidates] == ['教务处']
    assert candidates[0].source == '填报单位'


def test_reporting_unit_split_label_geometry():
    lines = [line('填报单位：', 80, 200, 120), line('教务处', 230, 200, 160),
             line('科研处', 600, 700)]
    assert ocrfields.extract_reporting_unit(lines, 1000, 1400)[0].text == '教务处'


def test_reporting_unit_missing_is_not_guessed():
    assert not ocrfields.extract_reporting_unit([line('某大学文件', 80, 30),
        line('教务处', 650, 900)], 1000, 1400)


def test_reporting_unit_multiple_candidates_needs_review():
    result = autoname.NameResult(0, unit_candidates=[
        ocrfields.FieldCandidate('教务处', .97, '填报单位'),
        ocrfields.FieldCandidate('科研处', .70, '填报单位')])
    assert not autoname.reporting_unit_auto_confirm(result)


def test_pending_boundary_blocks_named_export(tmp_path, make_scan):
    doc = load(make_scan('boundary.pdf', ['text'] * 3))
    g = Group(1, 3, 'A', 'B')
    g.boundary_confirmed = False
    doc.groups = [g]
    report = exporter.export_documents([doc], exporter.ExportOptions(out_dir=tmp_path / 'out', strict=False))
    assert not report.succeeded


def test_legacy_lenient_option_cannot_export_uncertain_name(tmp_path, make_scan):
    doc=load(make_scan("uncertain-name.pdf",["text"]))
    doc.groups=[Group(1,1,"看似正确的名称","教务处",unit_confirmed=False)]
    report=exporter.export_documents([doc],exporter.ExportOptions(out_dir=tmp_path/"out",strict=False))
    assert not report.succeeded
    assert report.failed


def test_manual_name_survives_regular_rerun(tmp_path, make_scan):
    doc = load(make_scan('manual.pdf', ['text']))
    g = Group(1, 1, '人工标题', '人工填报单位')
    g.title_manual = g.unit_manual = True
    doc.groups = [g]
    autoname.auto_name_groups(doc, None, engine=None)
    assert (g.title, g.unit) == ('人工标题', '人工填报单位')


def test_zero_manual_rotation_is_respected(tmp_path, make_scan, monkeypatch):
    from PIL import Image
    from scansplit.model import PageState
    ps = PageState(1, user_override_rotation=0)
    class Renderer:
        path = tmp_path / 'unused.pdf'
        def render(self, *a, **kw):
            assert kw.get('user_rotation', 0) == 0
            return Image.new('RGB', (100, 200), 'white')
    monkeypatch.setattr(autoname.ocr_cache, 'recognize_cached', lambda *a, **kw: [])
    monkeypatch.setattr(ocrfields, 'line_orientation', lambda ls: ('vertical', 1))
    autoname._ocr_page_rotation_aware(Renderer(), 0, object(), 1000, ps)


def test_manifest_tracks_retained_excluded_and_unselected(tmp_path, make_scan):
    import json
    doc=load(make_scan("trace.pdf",["text","blank","text","text"]))
    doc.pages[1].separator=True
    doc.manual_separator_pages.add(2)
    doc.groups=[Group(1,1,"A","B"),Group(3,4,"C","D")]
    report=exporter.export_documents([doc],exporter.ExportOptions(out_dir=tmp_path/"trace",only={(0,0)}))
    assert len(report.succeeded)==1
    data=json.loads(report.manifest_path.read_text(encoding="utf-8"))
    assert [p["status"] for p in data["sources"][0]["pages"]] == ["exported","excluded_manual","unselected","unselected"]
    assert data["sources"][0]["pages"][0]["output_page"]==1


def test_manual_text_not_lost_when_group_split(make_scan):
    doc=load(make_scan("splitmanual.pdf",["text"]*4))
    doc.groups=[Group(1,4,"用户专用名称","用户单位")]
    doc.groups[0].title_manual=doc.groups[0].unit_manual=True
    doc.pages[2].split_before=True
    grouping.refresh_groups(doc)
    assert all(any(c["text"]=="用户专用名称" and c["source"]=="人工填写" for c in g.title_candidates) for g in doc.groups)


def test_geometry_change_fails_fidelity(tmp_path,make_scan):
    from scansplit import verify
    source=make_scan("crop.pdf",["text"])
    reader=PdfReader(str(source))
    reader.pages[0].cropbox.upper_right=(100,100)
    writer=PdfWriter()
    writer.add_page(reader.pages[0])
    target=tmp_path/"changed-crop.pdf"
    with target.open("wb") as f:
        writer.write(f)
    assert any("裁切" in issue for issue in verify.verify_mapping(source,target,[1]))
