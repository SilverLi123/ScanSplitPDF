"""Form pagination is evidence, never a guessed or interpolated page number."""
from dataclasses import replace
import pytest
from scansplit import pagenum, jointsplit, pagination_read, ocr_cache
from tests.test_jointsplit import line, feature


@pytest.mark.parametrize('text,number,total', [
    ('页次：1',1,None),('页码: 3',3,None),('页 次 ： ２',2,None),('页次：2/5',2,5),
])
def test_form_labels_are_explicit_pagination(text, number, total):
    e=pagenum.pagination_evidence([line(text,cy=165,cx=600)],800,1000)
    assert (e.number,e.total,e.reliable,e.kind)==(number,total,True,'form_label')
    assert e.position == (.75,.165)


def test_table_body_numbers_do_not_create_false_pagination_conflicts():
    ls=[line('页次：1',cy=160),line('65',cy=880,cx=430),line('18',cy=880,cx=80),line('11',cy=880,cx=710)]
    e=pagenum.pagination_evidence(ls,800,1000)
    assert e.reliable and e.number==1


@pytest.mark.parametrize('other', [line('第2页',cy=100),line('2',cy=970),line('—2—',cy=970)])
def test_real_margin_pagination_conflicts_are_not_hidden(other):
    e=pagenum.pagination_evidence([line('页次：1',cy=160),other],800,1000)
    assert not e.reliable and e.number is None


@pytest.mark.parametrize('text,cy,score', [
    ('页次：1',300,.99),('第1页',165,.99),('1',165,.99),('页次：0',150,.99),
    ('页次：6/5',150,.99),('页次：1',150,.60),('页次管理规定',150,.99),
])
def test_body_numbers_invalid_labels_and_weak_single_lines_stay_uncertain(text,cy,score):
    assert not pagenum.pagination_evidence([line(text,cy=cy,score=score)],800,1000).reliable


@pytest.mark.parametrize('score',[float('nan'),float('inf'),1.1,-.1])
def test_invalid_ocr_scores_are_never_pagination_certainty(score):
    e=pagenum.pagination_evidence([line('页次：1',cy=160,score=score)],800,1000)
    assert not e.reliable and e.score==0


def forms(numbers=(1,2,3)):
    fs={}
    for p,n in enumerate(numbers,1):
        f=feature(n)
        f['pagination']=pagenum.pagination_evidence([line(f'页次：{n}',cy=160,score=.79)],800,1000)
        f['reporting_identity']=('教学设备统计表','教务处')
        fs[p]=f
    return fs


def test_three_actual_labels_cross_check_without_changing_ocr_scores():
    fs=forms()
    original=[f['pagination'] for f in fs.values()]
    assert not any(e.reliable for e in original)
    assert jointsplit.confirm_pagination_sequences(fs,[1,2,3],{},set())=={1,2,3}
    assert all(f['pagination'].reliable and f['pagination'].score==.79 for f in fs.values())
    assert all(not e.reliable for e in original)  # No original OCR evidence mutation.
    assert jointsplit.decide(fs[1],fs[2])[0]=='keep'


@pytest.mark.parametrize('change', [
    'missing','jump','reset','weak','duplicate','conflict','bare','no_identity','title','unit',
    'position_x','position_y','total','continuation','manual_split','manual_keep','separator','short',
])
def test_insufficient_or_conflicting_sequences_never_certify(change):
    fs=forms();manual={};separators=set();content=[1,2,3]
    e=fs[2]['pagination']
    if change=='missing':fs[2]['pagination']=pagenum.PaginationEvidence()
    elif change=='jump':fs[2]['pagination']=replace(e,number=8)
    elif change=='reset':fs[3]['pagination']=replace(fs[3]['pagination'],number=1)
    elif change=='weak':fs[2]['pagination']=replace(e,score=.60)
    elif change=='duplicate':fs[2]['pagination']=pagenum.pagination_evidence([line('页次：2',cy=160,score=.79)]*2,800,1000)
    elif change=='conflict':fs[2]['pagination']=pagenum.pagination_evidence([line('页次：2',cy=160),line('页次：3',cy=80)],800,1000)
    elif change=='bare':fs[2]['pagination']=replace(e,text='2',kind='number')
    elif change=='no_identity':fs[2]['reporting_identity']=None
    elif change=='title':fs[2]['reporting_identity']=('另一份表','教务处')
    elif change=='unit':fs[2]['reporting_identity']=('教学设备统计表','科研处')
    elif change=='position_x':fs[2]['pagination']=replace(e,position=(.8,.16))
    elif change=='position_y':fs[2]['pagination']=replace(e,position=(.5,.10))
    elif change=='total':fs[2]['pagination']=replace(e,total=4)
    elif change=='continuation':fs[2]['structure']['has_continuation']=True
    elif change=='manual_split':manual={2:True}
    elif change=='manual_keep':manual={2:False}
    elif change=='separator':
        fs[4]=fs.pop(3);content=[1,2,4];separators={3}
    elif change=='short':content=[1,2]
    assert not jointsplit.confirm_pagination_sequences(fs,content,manual,separators)
    assert not any(f['pagination'].reliable for f in fs.values())


def test_internal_blank_gap_does_not_invent_a_number_or_extra_boundary():
    fs=forms();fs[4]=fs.pop(3)
    assert jointsplit.confirm_pagination_sequences(fs,[1,2,4],{},set())=={1,2,4}
    assert jointsplit.decide(fs[2],fs[4],(3,))[0]=='keep'


def test_form_labels_do_not_need_extra_bare_digit_sequence_after_reset():
    fs=forms((3,1,2))
    for f in fs.values():f['pagination']=replace(f['pagination'],reliable=True)
    assert jointsplit.check_sequence(fs,[1,2,3],1,{},set(),'split','重启')[0]=='split'


def footers(numbers=(1,2,3)):
    fs=forms(numbers)
    for p,n in enumerate(numbers,1):
        fs[p]['pagination']=pagenum.pagination_evidence([line(f'—{n}—',score=.55)],800,1000)
        fs[p]['running_title']='关于教学设备清查的通知'
        fs[p]['reporting_identity']=None
    return fs


def test_decorated_footer_sequence_needs_independent_running_title_evidence():
    fs=footers()
    assert jointsplit.confirm_pagination_sequences(fs,[1,2,3],{},set())=={1,2,3}
    assert all(f['pagination'].reliable and f['pagination'].score==.55 for f in fs.values())


@pytest.mark.parametrize('change',[
    'bare','one_dash','title_missing','title_changed','unit_changed','unit_conflict',
    'weak','position','duplicate','jump','missing','manual','separator','short','year','nan','inf',
])
def test_footer_ocr_is_not_certified_without_all_cross_checks(change):
    fs=footers();manual={};separators=set();content=[1,2,3];e=fs[2]['pagination']
    if change=='bare':fs[2]['pagination']=replace(e,text='2',kind='number')
    elif change=='one_dash':fs[2]['pagination']=replace(e,text='2-',kind='number')
    elif change=='title_missing':fs[2]['running_title']=None
    elif change=='title_changed':fs[2]['running_title']='另一份教学报告'
    elif change=='unit_changed':fs[2]['units']={'科研处'}
    elif change=='unit_conflict':fs[2]['units']={'科研处','教务处'}
    elif change=='weak':fs[2]['pagination']=replace(e,score=.40)
    elif change=='year':
        for p,f in fs.items():f['pagination']=replace(f['pagination'],number=2025+p)
    elif change=='nan':fs[2]['pagination']=replace(e,score=float('nan'))
    elif change=='inf':fs[2]['pagination']=replace(e,score=float('inf'))
    elif change=='position':fs[2]['pagination']=replace(e,position=(.9,.97))
    elif change=='duplicate':fs[2]['pagination']=replace(e,ambiguous=True)
    elif change=='jump':fs[2]['pagination']=replace(e,number=8)
    elif change=='missing':fs[2]['pagination']=pagenum.PaginationEvidence()
    elif change=='manual':manual={2:False}
    elif change=='separator':
        fs[4]=fs.pop(3);content=[1,2,4];separators={3}
    elif change=='short':content=[1,2]
    assert not jointsplit.confirm_pagination_sequences(fs,content,manual,separators)
    assert not any(f['pagination'].reliable for f in fs.values())


def test_same_running_title_cannot_hide_a_reset_without_complete_footer_sequences():
    fs=footers((2,3,1,2))
    assert not jointsplit.confirm_pagination_sequences(fs,list(fs),{},set())
    assert jointsplit.decide(fs[2],fs[3])[0]=='review'


def test_landscape_header_retry_keeps_the_entire_label_and_uses_new_cache_key(make_scan):
    from PIL import Image
    from types import SimpleNamespace
    path=make_scan('landscape-label.pdf',['text'])
    class Renderer:
        def __init__(self):self.path=path
        def render(self,*a,**kw):return Image.new('RGB',(2400,1700),'white')
    class Engine:
        calls=0
        def recognize(self,im):
            self.calls+=1
            if im.height==306:return [line('页次：2',cy=265,cx=1300,score=.79)]
            return []
    renderer=Renderer();engine=Engine()
    old=ocr_cache.OcrCache.make_key(renderer,0,2400,90,'pagination-header-v1')
    ocr_cache.get_cache().put(old,[line('错误旧缓存',cy=30)])
    result=pagination_read.refine(renderer,0,engine,90,pagenum.PaginationEvidence())
    assert result.number==2 and result.kind=='form_label' and not result.reliable
    assert engine.calls==2


def test_duplicate_weak_candidates_cannot_be_replaced_by_a_clean_crop():
    base=pagenum.pagination_evidence([line('页次：1',cy=160,score=.79)]*2,800,1000)
    class NoRenderer:
        def render(self,*a,**kw):raise AssertionError('duplicate uncertainty erased')
    class Engine:
        def recognize(self,im):return []
    assert pagination_read.refine(NoRenderer(),0,Engine(),0,base)==base


@pytest.mark.parametrize('with_internal_blank',[False,True])
def test_pipeline_uses_observed_form_sequences_and_keeps_blank_deletion_independent(monkeypatch,make_scan,with_internal_blank):
    from tests.test_jointsplit import run_document
    from scansplit import pipeline
    kinds=['text']*6
    texts={p:f'页次：{(p-1)%3+1}' for p in range(1,7)}
    expected=[[1,2,3],[4,5,6]]
    if with_internal_blank:
        kinds.insert(2,'blank')
        texts={p+(p>=3):text for p,text in texts.items()}
        expected=[[1,2,4],[5,6,7]]
    doc,engine=run_document(monkeypatch,make_scan,kinds,texts)
    original=pipeline.autoname._ocr_page_rotation_aware
    def weak_label(*args):
        ls,w,h,q=original(*args)
        if ls[-1].text.startswith('页次'):ls[-1].score=.79
        return ls,w,h,q
    monkeypatch.setattr(pipeline.autoname,'_ocr_page_rotation_aware',weak_label)
    pipeline.analyze_document(doc,engine=engine,remove_blank=True)
    assert [g.pages for g in doc.groups]==expected and not doc.pending_boundaries
    assert doc.excluded_pages==({3} if with_internal_blank else set())
    assert all('交叉核对' in ev['reason'] for ev in doc.boundary_evidence.values())
