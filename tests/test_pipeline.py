from scansplit.pipeline import boundary_decision, update_boundary_state, analyze_document
from scansplit.model import Group, ScanDocument
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from scansplit.render import PageRenderer


def feature(title='教学检查统计表', number=None, unit='教务处', continuation=False):
    return dict(structure=dict(has_title=True,title_is_max=True,has_continuation=continuation,
        title_text=title),number=number,units={unit} if unit else set())


def test_same_form_different_reporting_unit_is_not_continuation():
    assert boundary_decision(feature(number=3), feature(number=1,unit='科研处'))[0] == 'split'


def test_same_form_same_unit_with_reset_needs_review():
    assert boundary_decision(feature(number=3), feature(number=1))[0] == 'review'


def test_same_form_continuing_numbers_kept():
    assert boundary_decision(feature(number=2), feature(number=3))[0] == 'keep'


def test_continuation_contradiction_needs_review():
    assert boundary_decision(feature(), feature(number=1,unit='科研处',continuation=True))[0] == 'review'


def test_new_title_without_number_needs_review():
    assert boundary_decision(feature(),feature(title='资产盘点汇总表'))[0] == 'review'


def test_pending_boundary_affects_both_neighbours(make_scan):
    doc = ScanDocument(make_scan('b.pdf',['text']*4), PdfReader(str(make_scan('b.pdf',['text']*4))))
    doc.groups = [Group(1,2,'A','B'),Group(3,4,'C','D')]
    doc.pending_boundaries[3] = 'review'
    doc.analysis_complete = True
    update_boundary_state(doc)
    assert all(not g.boundary_confirmed for g in doc.groups)


def test_missing_ocr_one_confirmation_cannot_certify_other_pages(make_scan):
    path=make_scan("fallback.pdf",["text","blank","text"])
    doc=ScanDocument(path,PdfReader(str(path)))
    class Unavailable:
        available=False
    analyze_document(doc,engine=Unavailable())
    assert set(doc.pending_boundaries)=={1,2,3}
    assert doc.pages[0].has_rotation_proposal and doc.pages[2].has_rotation_proposal
    doc.pending_boundaries.pop(1)
    doc.pages[0].rotation_proposal=None
    update_boundary_state(doc)
    assert not doc.groups[0].boundary_confirmed


def test_pdfium_stored_rotation_not_applied_twice(tmp_path, make_scan):
    path = make_scan('original.pdf',['text'])
    src = PdfReader(str(path))
    src.pages[0][NameObject('/Rotate')] = NumberObject(90)
    writer = PdfWriter()
    writer.add_page(src.pages[0])
    rotated = tmp_path/'rotated.pdf'
    with open(rotated,'wb') as f:
        writer.write(f)
    renderer = PageRenderer(rotated)
    try:
        image = renderer.render(0,scale=1)
        assert image.width > image.height  # stored rotation alone makes portrait landscape
        assert renderer.display_size_pt(0)[0] > renderer.display_size_pt(0)[1]
        extra = renderer.render(0,scale=1,user_rotation=90)
        assert extra.height > extra.width
    finally:
        renderer.close()


def test_offline_failure_preserves_all_pages(make_scan):
    path = make_scan('noengine.pdf',['text','blank','text'])
    doc = ScanDocument(path,PdfReader(str(path)))
    class Unavailable:
        available=False
    analyze_document(doc,engine=Unavailable())
    assert [p for g in doc.groups for p in g.pages] == [1,2,3]
    assert all(not g.boundary_confirmed for g in doc.groups)
