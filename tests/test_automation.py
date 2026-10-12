"""Automation must remove clicks without silently accepting uncertainty."""
from types import SimpleNamespace
import pytest
from pypdf import PdfReader
from scansplit import automation, pagination_read, pagenum, pipeline, jointsplit, ocr_cache
from scansplit.autoname import DirectionResult
from scansplit.model import ScanDocument, Group
from scansplit.render import PageRenderer
from scansplit.ocr import OcrLine
from tests.test_jointsplit import feature, line


def upright():
    def q(n=0,score=0,horizontal=0,quality=0,substance=0):
        return dict(n_lines=n,avg_score=score,horizontal_frac=horizontal,quality=quality,substance_frac=substance)
    return DirectionResult(1,{0:q(4,.86,1,.17,1),90:q(4,.87,0,.04,1),180:q(),270:q()},0,confidence=.65)


def test_full_direction_comparison_can_certify_maintaining_upright():
    r=upright()
    assert automation.clearly_upright(r)
    assert r.confidence==.65 and r.suggested_angle==0


@pytest.mark.parametrize('change',[
    'nonzero','missing','opposite','horizontal_rival','side_quality','few_lines','low_score','mixed','no_substance','no_quality','nan',
])
def test_rotation_or_competing_or_sparse_evidence_still_requires_confirmation(change):
    r=upright()
    if change=='nonzero': r.suggested_angle=90
    elif change=='missing': r.qualities.pop(180)
    elif change=='opposite': r.qualities[180]['quality']=.10
    elif change=='horizontal_rival': r.qualities[90]['horizontal_frac']=.8
    elif change=='side_quality': r.qualities[90]['quality']=.1
    elif change=='nan': r.qualities[180]['quality']=float('nan')
    else:
        key,value={'few_lines':('n_lines',2),'low_score':('avg_score',.79),'mixed':('horizontal_frac',.8),
                   'no_substance':('substance_frac',.5),'no_quality':('quality',0)}[change]
        r.qualities[0][key]=value
    assert not automation.clearly_upright(r)


def margin_engine(number=1,total=None,score=.95,header_number=1):
    class Engine:
        available=True
        calls=0
        def recognize(self,image):
            self.calls+=1
            text=f'第{number}页'+(f'，共{total}页' if total else '')
            # Header input is slightly taller than the footer crop.
            is_header=self.calls%2==1
            if is_header:
                return [line(f'第{header_number}页',cy=30,cx=image.width/2,score=score)] if header_number else []
            return [line(text,cy=image.height-30,cx=image.width/2,score=score)]
    return Engine()


def test_margin_retry_preserves_geometry_source_and_cache(make_scan):
    path=make_scan('margin.pdf',['text'])
    original=path.read_bytes()
    renderer=PageRenderer(path)
    engine=margin_engine()
    base=pagenum.PaginationEvidence(1,None,False,'第1页','低分')
    try:
        improved=pagination_read.refine(renderer,0,engine,0,base)
        assert improved.reliable and improved.number==1 and '页边区域' in improved.reason
        assert engine.calls==2
        assert pagination_read.refine(renderer,0,engine,0,base)==improved and engine.calls==2
        pagination_read.refine(renderer,0,engine,90,base)
        assert engine.calls==4  # Angles never share a cropped OCR cache key.
    finally:
        renderer.close()
    assert path.read_bytes()==original


def test_empty_region_is_retried_instead_of_masking_temporary_failure(make_scan):
    renderer=PageRenderer(make_scan('empty-header.pdf',['text']))
    engine=margin_engine(header_number=None)
    base=pagenum.PaginationEvidence(1,None,False,'第1页')
    try:
        assert pagination_read.refine(renderer,0,engine,0,base).reliable and engine.calls==2
        assert pagination_read.refine(renderer,0,engine,0,base).reliable and engine.calls==3
    finally:
        renderer.close()


def test_unknown_regional_candidate_is_exposed_as_uncertain(make_scan):
    renderer=PageRenderer(make_scan('weak-region.pdf',['text']))
    try:
        result=pagination_read.refine(renderer,0,margin_engine(score=.60),0,pagenum.PaginationEvidence())
        assert result.number==1 and not result.reliable and '待确认' in result.reason
    finally:
        renderer.close()


@pytest.mark.parametrize('base,number,total,header,score',[
    (pagenum.PaginationEvidence(1,None,False,'第1页'),2,None,None,.95),
    (pagenum.PaginationEvidence(1,2,False,'第1页共2页'),1,3,None,.95),
    (pagenum.PaginationEvidence(),1,None,2,.95),
    (pagenum.PaginationEvidence(1,None,False,'第1页'),1,None,None,.70),
])
def test_regional_ocr_conflicts_and_low_scores_do_not_promote(make_scan,base,number,total,header,score):
    renderer=PageRenderer(make_scan('conflict.pdf',['text']))
    try:
        result=pagination_read.refine(renderer,0,margin_engine(number,total,score,header),0,base)
        assert not result.reliable
    finally:
        renderer.close()


@pytest.mark.parametrize('base,canceled',[
    (pagenum.PaginationEvidence(1,None,True,'第1页'),False),
    (pagenum.PaginationEvidence(text='第1页；第2页',reason='冲突'),False),
    (pagenum.PaginationEvidence(),True),
])
def test_reliable_conflicting_or_canceled_work_never_rerenders(base,canceled):
    class NoRenderer:
        def render(self,*a,**k): raise AssertionError('unexpected render')
    assert pagination_read.refine(NoRenderer(),0,margin_engine(),0,base,lambda:canceled)==base


@pytest.mark.parametrize('numbers,manual,expected',[
    ([1,2,3,1,2],{},'split'),([1,3,1,2],{},'review'),([2,3,1,4],{},'review'),
    ([1,3,1,2],{2:True},'split'),([2,3,1,4],{4:True},'split'),
])
def test_sequence_vetoes_contradictions_without_crossing_manual_starts(numbers,manual,expected):
    fs={i+1:feature(n) for i,n in enumerate(numbers)}
    content=list(fs)
    index=max(i for i in range(1,len(content)) if numbers[i]==1)
    action,reason,sequence=jointsplit.check_sequence(fs,content,index,manual,set(),'split','重启')
    assert action==expected and sequence


def test_unknown_neighbour_number_is_never_fabricated():
    fs={1:feature(3),2:feature(1),3:feature(None,reliable=False)}
    action,_,seq=jointsplit.check_sequence(fs,[1,2,3],1,{},set(),'split','重启')
    assert action=='split' and seq[-1]['number'] is None and not seq[-1]['reliable']


@pytest.mark.parametrize('numbers,expected',[
    ([3,1],'review'),([2,3,1,2],'split'),([2,3,1,4],'review'),
])
def test_bare_footer_digits_need_a_sequence_to_avoid_table_cell_resets(numbers,expected):
    fs={i+1:feature(n) for i,n in enumerate(numbers)}
    for p,f in fs.items():
        f['pagination']=pagenum.PaginationEvidence(numbers[p-1],None,True,str(numbers[p-1]))
    index=numbers.index(1)
    action,_,_=jointsplit.check_sequence(fs,list(fs),index,{},set(),'split','重启')
    assert action==expected


@pytest.mark.parametrize('labels',[('Page3','Page1'),('第3页','第1页')])
def test_explicit_page_labels_do_not_require_another_title_or_blank(labels):
    fs={1:feature(3),2:feature(1)}
    for p,text in enumerate(labels,1):
        fs[p]['pagination']=pagenum.PaginationEvidence(3 if p==1 else 1,None,True,text)
    assert jointsplit.check_sequence(fs,[1,2],1,{},set(),'split','重启')[0]=='split'


def doc(make_scan):
    p=make_scan('ready.pdf',['text']*4)
    d=ScanDocument(p,PdfReader(p))
    d.analysis_complete=True
    d.groups=[Group(1,2,boundary_confirmed=True),Group(3,4,boundary_confirmed=False)]
    return d


def test_ready_groups_named_automatically_but_uncertain_groups_skipped(monkeypatch,make_scan):
    d=doc(make_scan)
    calls=[]
    def name(document,renderer,**kw):
        calls.append(kw)
        document.groups[0].title='自动标题'
        return ['result']
    monkeypatch.setattr(automation.autoname,'auto_name_groups',name)
    assert automation.name_ready(d,SimpleNamespace(available=True))==['result']
    assert calls[0]['only_indices']=={0} and calls[0]['preserve_confirmed'] and calls[0]['unit_mode']=='reporting'
    assert d.groups[0].title=='自动标题' and not d.groups[1].title and not d.naming_attempted
    d.groups[1].boundary_confirmed=True
    automation.name_ready(d,SimpleNamespace(available=True))
    assert d.naming_attempted


@pytest.mark.parametrize('state',['stale','unanalysed','pending_direction','unavailable','cancel','pending_boundary'])
def test_unready_or_unavailable_work_does_not_name(monkeypatch,make_scan,state):
    d=doc(make_scan)
    if state=='stale': d.analysis_stale=True
    if state=='unanalysed': d.analysis_complete=False
    if state=='pending_direction': d.pages[0].rotation_proposal=90
    if state=='pending_boundary': d.pending_boundaries={3:'unresolved'}
    def forbidden(*a,**kw): raise AssertionError('unexpected naming')
    monkeypatch.setattr(automation.autoname,'auto_name_groups',forbidden)
    assert automation.name_ready(d,SimpleNamespace(available=state!='unavailable'),cancel=lambda:state=='cancel')==[]


@pytest.mark.parametrize('failure',['cancel','error'])
def test_organizing_is_atomic_and_preserves_manual_decisions(monkeypatch,make_scan,failure):
    d=doc(make_scan)
    d.groups[0].title='人工标题';d.groups[0].title_manual=True
    d.manual_boundaries={3:True}
    before=d.snapshot()
    canceled=[False]
    def analyze(document,*a):
        document.groups[0].title='不应保留'
    def name(document,*a):
        if failure=='error': raise RuntimeError('expected')
        canceled[0]=True
    monkeypatch.setattr(pipeline,'analyze_document',analyze)
    monkeypatch.setattr(automation,'name_ready',name)
    if failure=='error':
        with pytest.raises(RuntimeError): pipeline.organize_document(d,engine=SimpleNamespace(available=True))
    else:
        assert not pipeline.organize_document(d,engine=SimpleNamespace(available=True),cancel=lambda:canceled[0])
    assert d.snapshot()==before
