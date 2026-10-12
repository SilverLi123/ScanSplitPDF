"""Boundary identity uses explicit OCR evidence; naming remains unconfirmed."""
from dataclasses import replace
import pytest
from scansplit import jointsplit, ocrfields, pipeline
from tests.test_jointsplit import line


def evidence():
    ls = [line('实验教学中心巡查记录表',cy=80,score=.90),
          line('填报单位：实验中心',cy=160,score=.87),line('正文数据',cy=500)]
    ls[0].box=[(345,60),(455,60),(455,100),(345,100)]
    st = ocrfields.first_page_structure(ls,800,1000)
    us = ocrfields.extract_reporting_unit(ls,800,1000)
    return ls,st,us


def test_readable_form_identity_is_not_the_naming_candidate_score():
    ls,st,us = evidence()
    original = us[0].to_dict()
    assert us[0].confidence < .85
    assert jointsplit.form_identity(ls,800,1000,st,us) == ('实验教学中心巡查记录表','实验中心')
    assert us[0].to_dict() == original and ls[1].score == .87


@pytest.mark.parametrize('change',[
    'title_weak','unit_weak','title_nan','unit_inf','unit_invalid','title_duplicate',
    'unit_duplicate','unit_changed','wrong_role','unlabelled','body_unit','small_title',
    'body_title','offcentre_title','multiple_titles','no_title','not_largest','continuation',
    'section','no_unit','conflicting_units','no_bbox','invalid_bbox','width','height',
    'unsupported_value','negative_geometry',
])
def test_form_identity_never_approves_incomplete_or_conflicting_sources(change):
    ls,st,us = evidence()
    if change == 'title_weak':ls[0].score=.84
    elif change == 'unit_weak':ls[1].score=.84
    elif change == 'title_nan':ls[0].score=float('nan')
    elif change == 'unit_inf':ls[1].score=float('inf')
    elif change == 'unit_invalid':ls[1].score=1.1
    elif change == 'title_duplicate':ls.append(replace(ls[0]))
    elif change == 'unit_duplicate':ls.append(replace(ls[1]))
    elif change == 'unit_changed':ls[1].text='填报单位：资产管理处'
    elif change == 'wrong_role':us[0].source='落款'
    elif change == 'unlabelled':ls[1].text='实验中心'
    elif change == 'body_unit':
        ls[1]=line('填报单位：实验中心',cy=500,score=.95)
        us=ocrfields.extract_reporting_unit(ls,800,1000)
    elif change == 'small_title':ls[0].box=[(345,75),(455,75),(455,85),(345,85)]
    elif change == 'body_title':ls[0]=line(st['title_text'],cy=300)
    elif change == 'offcentre_title':ls[0]=line(st['title_text'],cy=80,cx=100)
    elif change == 'multiple_titles':st['title_text']+='安全检查报告'
    elif change == 'no_title':st['has_title']=False
    elif change == 'not_largest':st['title_is_max']=False
    elif change == 'continuation':st['has_continuation']=True
    elif change == 'section':st['has_section']=True
    elif change == 'no_unit':us=[]
    elif change == 'conflicting_units':us.append(replace(us[0],text='资产管理处'))
    elif change == 'no_bbox':us[0].bbox=None
    elif change == 'invalid_bbox':us[0].bbox=[0,0,float('nan'),20]
    elif change == 'unsupported_value':us[0].text='资产管理处'
    elif change == 'negative_geometry':ls[0].box=[(-1,70),(455,70),(455,90),(-1,90)]
    width = 0 if change == 'width' else 800
    height = float('inf') if change == 'height' else 1000
    assert jointsplit.form_identity(ls,width,height,st,us) is None


def test_separate_label_and_value_both_need_strong_actual_ocr():
    ls,st,_=evidence()
    ls[1]=line('填报单位：',cy=160,cx=250,score=.90)
    ls.append(line('实验中心',cy=160,cx=380,score=.88))
    us=ocrfields.extract_reporting_unit(ls,800,1000)
    assert jointsplit.form_identity(ls,800,1000,st,us)==('实验教学中心巡查记录表','实验中心')
    ls[1].score=.84
    assert jointsplit.form_identity(ls,800,1000,st,us) is None


def test_pipeline_cross_checks_form_headers_without_confirming_names(monkeypatch,make_scan):
    from tests.test_jointsplit import run_document
    doc,engine=run_document(monkeypatch,make_scan,['text']*6,
                            {p:f'页次：{(p-1)%3+1}' for p in range(1,7)})
    original=pipeline.autoname._ocr_page_rotation_aware
    def read(*args):
        ls,w,h,q=original(*args)
        ls[0].text='实验教学中心巡查记录表'
        ls[1].text='填报单位：实验中心';ls[1].score=.87
        ls[-1].score=.79
        return ls,w,h,q
    monkeypatch.setattr(pipeline.autoname,'_ocr_page_rotation_aware',read)
    pipeline.analyze_document(doc,engine=engine,remove_blank=True)
    assert [g.pages for g in doc.groups]==[[1,2,3],[4,5,6]]
    assert not doc.pending_boundaries
    assert all(not g.title and not g.unit for g in doc.groups)


def test_existing_strong_multiline_identity_still_supports_sequences(monkeypatch,make_scan):
    from tests.test_jointsplit import run_document
    doc,engine=run_document(monkeypatch,make_scan,['text']*6,
                            {p:f'页次：{(p-1)%3+1}' for p in range(1,7)})
    original=pipeline.autoname._ocr_page_rotation_aware
    def read(*args):
        ls,w,h,q=original(*args)
        ls[0].text='2026年度实验室'
        ls[0].box=[(345,50),(455,50),(455,90),(345,90)]
        second=line('安全检查报告',cy=120)
        second.box=[(345,100),(455,100),(455,140),(345,140)]
        ls.insert(1,second);ls[-1].score=.79
        st=ocrfields.first_page_structure(ls,w,h)
        units=ocrfields.extract_reporting_unit(ls,w,h)
        assert jointsplit.form_identity(ls,w,h,st,units) is None
        assert ocrfields.extract_title(ls,w,h)[0].confidence >= .85
        return ls,w,h,q
    monkeypatch.setattr(pipeline.autoname,'_ocr_page_rotation_aware',read)
    pipeline.analyze_document(doc,engine=engine,remove_blank=True)
    assert [g.pages for g in doc.groups]==[[1,2,3],[4,5,6]]
    assert not doc.pending_boundaries
