"""Printed pagination decisions, real PDF page retention and manual priority."""
import pytest
from pypdf import PdfReader
from scansplit import pagenum, jointsplit, pipeline, exporter, verify
from scansplit.model import ScanDocument, Group
from scansplit.ocr import OcrLine


def line(text, cy=970, cx=400, score=.95):
    return OcrLine(text=text, score=score, box=[(cx-55,cy-10),(cx+55,cy-10),(cx+55,cy+10),(cx-55,cy+10)])


@pytest.mark.parametrize('text,number,total',[
    ('第 3 页',3,None),('第3页，共5页',3,5),('Page 2 of 4',2,4),
    ('４ / １２',4,12),('— 7 —',7,None),('8',8,None),
])
def test_strict_printed_formats(text,number,total):
    e=pagenum.pagination_evidence([line(text)],800,1000)
    assert (e.number,e.total,e.reliable)==(number,total,True)


@pytest.mark.parametrize('lines',[
    [line('2026年10月12日')], [line('2026')], [line('5',cy=880,cx=100)],
    [line('第1页',score=.60)], [line('第1页',cy=500)],
    [line('第4页，共3页')], [line('第1页',cy=50),line('第2页')],
    [line('第1页'),line('第1页',score=.60)],
])
def test_ambiguous_dates_body_cells_and_weak_ocr_do_not_qualify(lines):
    assert not pagenum.pagination_evidence(lines,800,1000).reliable


def feature(number, *,total=None,title='教学设备统计表',continuation=False,units=('教务处',),reliable=True):
    return dict(pagination=pagenum.PaginationEvidence(number,total,reliable),number=number,
        structure=dict(title_text=title,has_title=bool(title),title_is_max=True,has_continuation=continuation),
        units=set(units))


@pytest.mark.parametrize('gap',[(3,),(),(3,4)])
def test_reliable_restart_splits_with_or_without_blank_or_different_title(gap):
    action,reason=jointsplit.decide(feature(3),feature(1),gap)
    assert action=='split' and '3 → 1' in reason


@pytest.mark.parametrize('before,after,gap',[
    (feature(2),feature(3),(3,)),
    (feature(2),feature(3,continuation=True),(3,4)),
    (feature(2,total=4),feature(3,total=4),()),
])
def test_continuity_retains_internal_blank(before,after,gap):
    assert jointsplit.decide(before,after,gap)[0]=='keep'


@pytest.mark.parametrize('before,after,gap',[
    (feature(3),feature(1,continuation=True),(3,)),
    (feature(3),feature(1,title='附件：统计明细'),()),
    (feature(2),feature(3,units=('科研处',)),(3,)),
    (feature(3,total=5),feature(1),(3,)),
    (feature(3),feature(2),(3,)),
    (feature(2),feature(4),(3,)),
    (feature(1),feature(1),()),
    (feature(2,total=3),feature(3,total=4),(3,)),
    (feature(3),feature(1,units=('教务处','科研处')),(3,)),
    (feature(None,reliable=False),feature(1),(3,)),
    (feature(2),feature(3,title='资产盘点汇总表'),(3,)),
    (feature(2,reliable=False),feature(3),()),
    (feature(2),feature(None,reliable=False,continuation=True),()),
])
def test_conflicts_never_auto_split_or_certify_continuation(before,after,gap):
    assert jointsplit.decide(before,after,gap)[0]=='review'


def test_one_page_documents_and_restarted_continuations():
    assert jointsplit.decide(feature(1,total=1),feature(1),())[0]=='split'
    assert jointsplit.decide(feature(1),feature(1),(2,))[0]=='split'


def run_document(monkeypatch,make_scan,kinds,texts):
    source=make_scan('joint.pdf',kinds)
    doc=ScanDocument(source,PdfReader(source))
    # Isolate boundary rules from orientation score changes; real OCR separately accepted.
    monkeypatch.setattr(pipeline.autoname,'analyze_directions',lambda d,r,pages,**kw:
        [pipeline.autoname.DirectionResult(page=p,qualities={},suggested_angle=0,confidence=1) for p in pages])
    def read(renderer,index,engine,width,ps):
        page_text=texts.get(index+1)
        lines=[line('教学设备统计表',cy=80),line('填报单位：教务处',cy=200),line('正文数据',cy=500)]
        if page_text:
            lines.append(line(page_text))
        return lines,800,1000,None
    monkeypatch.setattr(pipeline.autoname,'_ocr_page_rotation_aware',read)
    class Engine:
        available=True
    return doc,Engine()


@pytest.mark.parametrize('kinds,texts,expected',[
    (['text','text','blank','text','text'],{1:'第1页',2:'第2页',4:'第1页',5:'第2页'},[(1,3),(4,5)]),
    (['text','text','blank','blank','text','text'],{1:'第1页',2:'第2页',5:'第1页',6:'第2页'},[(1,4),(5,6)]),
    (['text','text','text','text'],{1:'第1页',2:'第2页',3:'第1页',4:'第2页'},[(1,2),(3,4)]),
    (['text','blank','text'],{1:'第2页',3:'第3页'},[(1,3)]),
])
def test_pipeline_and_lossless_export_keep_every_original_page(monkeypatch,make_scan,tmp_path,kinds,texts,expected):
    doc,engine=run_document(monkeypatch,make_scan,kinds,texts)
    before=verify.file_sha256(doc.path)
    pipeline.analyze_document(doc,engine=engine)
    assert [(g.start,g.end) for g in doc.groups]==expected
    assert not doc.pending_boundaries and all(g.boundary_confirmed for g in doc.groups)
    assert not doc.separators
    assert [p for g in doc.groups for p in g.pages]==list(range(1,len(kinds)+1))
    for i,g in enumerate(doc.groups):
        g.title,g.unit=f'报告{i+1}','教务处'
        g.title_confirmed=g.unit_confirmed=True
    report=exporter.export_documents([doc],exporter.ExportOptions(out_dir=tmp_path/'export'))
    assert len(report.succeeded)==len(expected) and not report.failed and not report.coverage_issues
    assert report.manifest_path
    assert verify.file_sha256(doc.path)==before
    snapshot=doc.snapshot()
    doc.boundary_evidence.clear()
    doc.restore(snapshot)
    assert doc.boundary_evidence==snapshot['boundary_evidence']


def test_blank_alone_remains_pending_and_clears_previous_auto_point(monkeypatch,make_scan):
    doc,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第2页'})
    doc.pages[2].split_before=True
    pipeline.analyze_document(doc,engine=engine)
    assert 3 in doc.pending_boundaries and not doc.pages[2].split_before
    assert not doc.separators and [(g.start,g.end) for g in doc.groups]==[(1,3)]


@pytest.mark.parametrize('split',[False,True])
def test_manual_boundary_and_names_survive_reanalysis(monkeypatch,make_scan,split):
    doc,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第3页',3:'第1页'})
    doc.manual_boundaries={3:split}
    doc.pages[2].split_before=split
    doc.manual_separator_pages={2}
    doc.groups=[Group(1,2 if split else 3,'人工标题','人工单位',title_manual=True,unit_manual=True)]
    pipeline.analyze_document(doc,engine=engine)
    assert doc.pages[2].split_before==split and 3 not in doc.pending_boundaries
    assert not doc.pages[1].separator
    assert doc.groups[0].title=='人工标题' and doc.groups[0].unit=='人工单位'


def test_suspected_blank_and_direction_pending_never_certify_restart(monkeypatch,make_scan):
    from scansplit.model import BlankResult
    doc,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第3页',3:'第1页'})
    monkeypatch.setattr(pipeline.blankdetect,'detect_document',lambda *a,**kw:[BlankResult('content'),BlankResult('maybe'),BlankResult('content')])
    pipeline.analyze_document(doc,engine=engine)
    assert not doc.pages[2].split_before and 3 in doc.pending_boundaries
    monkeypatch.undo()
    doc,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第3页',3:'第1页'})
    doc.pages[2].rotation_proposal=90
    monkeypatch.setattr(pipeline.autoname,'analyze_directions',lambda d,r,pages,**kw:
        [pipeline.autoname.DirectionResult(page=p,qualities={},suggested_angle=None if p==3 else 0,confidence=1) for p in pages])
    pipeline.analyze_document(doc,engine=engine)
    assert not doc.pages[2].split_before and 3 in doc.pending_boundaries


def test_unknown_numbers_do_not_auto_split_without_blank(monkeypatch,make_scan):
    doc,engine=run_document(monkeypatch,make_scan,['text','text'],{1:'第3页'})
    pipeline.analyze_document(doc,engine=engine)
    assert 2 in doc.pending_boundaries and not doc.pages[1].split_before


def test_weak_page_numbers_cannot_certify_continuous_pages(monkeypatch,make_scan):
    doc,engine=run_document(monkeypatch,make_scan,['text','text'],{1:'第2页',2:'第3页'})
    read=pipeline.autoname._ocr_page_rotation_aware
    def weak(*args):
        lines,w,h,quality=read(*args)
        lines[-1].score=.70
        return lines,w,h,quality
    monkeypatch.setattr(pipeline.autoname,'_ocr_page_rotation_aware',weak)
    pipeline.analyze_document(doc,engine=engine)
    assert 2 in doc.pending_boundaries and not doc.groups[0].boundary_confirmed


def test_evidence_undo_snapshot_is_independent_and_old_snapshots_compatible(make_scan):
    source=make_scan('snapshot.pdf',['text','text'])
    doc=ScanDocument(source,PdfReader(source))
    doc.boundary_evidence={2:dict(blank_pages=[1],next_pagination=dict(number=1))}
    snapshot=doc.snapshot()
    doc.boundary_evidence[2]['blank_pages'].clear()
    doc.boundary_evidence[2]['next_pagination']['number']=5
    doc.restore(snapshot)
    assert doc.boundary_evidence[2]['blank_pages']==[1]
    assert doc.boundary_evidence[2]['next_pagination']['number']==1
    doc.boundary_evidence[2]['next_pagination']['number']=8
    assert snapshot['boundary_evidence'][2]['next_pagination']['number']==1
    snapshot.pop('boundary_evidence')
    doc.restore(snapshot)
    assert not doc.boundary_evidence
