"""Blank removal is an explicit page disposition, never an implicit split."""
import json
from types import SimpleNamespace
import numpy as np
import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader
from scansplit import blankdetect, blankpolicy, exporter, grouping, jointsplit, pipeline, presentation, verify, workflow
from scansplit.model import BlankResult, Group, ScanDocument
from scansplit.render import PageRenderer
from tests.test_jointsplit import feature, run_document
from tests.test_ui_smoke import ui_env, _make_root


@pytest.fixture
def blank_app(ui_env):
    root=_make_root()
    app=ui_env.App(root)
    try:
        yield app
    finally:
        app.on_close()


def document(path):
    return ScanDocument(path, PdfReader(path))


def apply(doc):
    r=PageRenderer(doc.path)
    try:
        for i,p in enumerate(doc.pages):
            p.blank=blankdetect.detect_page(np.asarray(r.render_detect(i).convert('L')),blankdetect.DetectParams())
        blankpolicy.apply(doc,r)
    finally:
        r.close()


@pytest.mark.parametrize('kind',['blank','shade','backside'])
def test_verified_blank_removal_does_not_create_a_boundary(make_scan,kind):
    d=document(make_scan('internal.pdf',['text',kind,'text']))
    apply(d)
    grouping.auto_group(d)
    assert d.omitted_blanks=={2} and not d.separators
    assert [(g.start,g.end,g.pages,g.page_count) for g in d.groups]==[(1,3,[1,3],2)]
    assert blankpolicy.valid_exclusion(d.pages[1])
    assert not exporter.business_integrity_issues(d)


@pytest.mark.parametrize('kind',['stamp','text','table','edge_lines'])
def test_content_and_scanner_edges_are_not_deleted_without_clear_evidence(make_scan,kind):
    d=document(make_scan('protect.pdf',[kind]))
    apply(d)
    assert not d.omitted_blanks


def test_uncropped_check_protects_footer_only_page(tmp_path):
    image=Image.new('RGB',(1240,1754),'white')
    ImageDraw.Draw(image).rectangle((540,1720,700,1730),fill='black')
    path=tmp_path/'footer.pdf';image.save(path,'PDF')
    d=document(path)
    r=PageRenderer(path)
    try:
        assert blankdetect.detect_page(np.asarray(r.render_detect(0).convert('L')),blankdetect.DetectParams()).is_blank
        d.pages[0].blank=BlankResult('blank',.99)
        blankpolicy.apply(d,r)
        assert not d.pages[0].omitted_blank and d.pages[0].blank.verdict!='blank'
    finally:
        r.close()


def test_short_text_layer_never_disappears(make_text):
    d=document(make_text('text.pdf',[[('7',550,8,9)]]))
    d.pages[0].blank=BlankResult('blank',.99)
    r=PageRenderer(d.path)
    try: blankpolicy.apply(d,r)
    finally: r.close()
    assert not d.omitted_blanks and d.pages[0].blank.verdict=='content'


@pytest.mark.parametrize('mode',['failure','cancel','manual_keep','manual_remove'])
def test_failure_cancel_and_manual_decisions_never_become_auto_exclusions(make_scan,mode):
    d=document(make_scan('manual.pdf',['blank']))
    d.pages[0].blank=BlankResult('blank',.99)
    if mode.startswith('manual'):
        d.manual_separator_pages={1}
        d.pages[0].separator=mode=='manual_remove'
    renderer=SimpleNamespace(render=lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('render failed')))
    blankpolicy.apply(d,renderer,cancel=lambda:mode=='cancel')
    assert not d.omitted_blanks
    assert d.pages[0].separator==(mode=='manual_remove')


def test_snapshot_and_manual_name_survive_internal_removal(make_scan):
    d=document(make_scan('snapshot.pdf',['text','blank','text']))
    d.groups=[Group(1,3,'人工标题','教务处',title_manual=True,unit_manual=True)]
    apply(d);grouping.auto_group(d)
    assert d.groups[0].pages==[1,3] and d.groups[0].title=='人工标题'
    snapshot=d.snapshot()
    d.pages[1].blank_exclusion_evidence['signals'].clear()
    d.groups[0].excluded_pages=()
    d.restore(snapshot)
    assert blankpolicy.valid_exclusion(d.pages[1]) and d.groups[0].pages==[1,3]
    d.pages[1].blank_exclusion_evidence['signals'].clear()
    assert snapshot['pages'][1]['blank_exclusion_evidence']['signals']


@pytest.mark.parametrize('mode',['flag_only','unknown_hole','contradiction','invalid_evidence','empty_group'])
def test_export_rejects_unexplained_or_forged_page_exclusions(make_scan,tmp_path,mode):
    d=document(make_scan('guard.pdf',['text','blank','text']))
    d.groups=[Group(1,3,'报告','教务处')]
    if mode=='flag_only':
        d.pages[1].omitted_blank=True;d.groups[0].excluded_pages=(2,)
    elif mode=='unknown_hole': d.groups[0].excluded_pages=(1,)
    elif mode=='empty_group': d.groups[0].excluded_pages=(1,2,3)
    else:
        apply(d)
        if mode=='invalid_evidence':
            grouping.auto_group(d)
            d.pages[1].blank_exclusion_evidence['signals']['dark_ratio']=float('nan')
    assert exporter.business_integrity_issues(d)
    report=exporter.export_documents([d],exporter.ExportOptions(out_dir=tmp_path/'blocked',strict=False,only={(0,0)}))
    assert not report.succeeded


def test_internal_blank_lossless_export_and_page_destination_map(make_scan,tmp_path):
    d=document(make_scan('export.pdf',['text','blank','text']))
    original=verify.file_sha256(d.path)
    apply(d);grouping.auto_group(d)
    d.analysis_complete=True
    d.groups[0].title,d.groups[0].unit='报告','教务处'
    report=exporter.export_documents([d],exporter.ExportOptions(out_dir=tmp_path/'out'))
    assert len(report.succeeded)==1 and not report.failed and not report.coverage_issues
    result=report.succeeded[0]
    assert result.pages==[1,3] and len(PdfReader(result.path).pages)==2
    assert not verify.verify_mapping(d.path,result.path,[1,3],rotations=[0,0])
    assert verify.file_sha256(d.path)==original
    pages=json.loads(report.manifest_path.read_text(encoding='utf-8'))['sources'][0]['pages']
    assert [p['status'] for p in pages]==['exported','excluded_blank_rule','exported']
    assert [p['output_page'] for p in pages]==[1,None,2]
    assert pages[1]['output'] is None and pages[1]['blank_exclusion_evidence']


@pytest.mark.parametrize('kinds,expected',[
    (['blank','text','blank'],[(2,2)]),
    (['blank','blank'],[]),
    (['text','blank','blank','text'],[(1,4)]),
])
def test_leading_trailing_and_consecutive_blanks_do_not_make_empty_files(make_scan,kinds,expected):
    d=document(make_scan('trim.pdf',kinds));apply(d);grouping.auto_group(d)
    assert [(g.start,g.end) for g in d.groups]==expected
    assert sorted(p for g in d.groups for p in g.pages)==[i+1 for i,k in enumerate(kinds) if k=='text']
    assert not exporter.business_integrity_issues(d)
    d.analysis_complete=True
    if not d.groups:
        assert workflow.next_step([d]).action!='export'


def test_continuous_pagination_keeps_one_file_while_removing_internal_blank(monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第1页',3:'第2页'})
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    assert [g.pages for g in d.groups]==[[1,3]] and not d.pending_boundaries
    assert d.groups[0].boundary_confirmed and not d.pages[2].split_before


def test_manual_merge_keeps_automatic_blank_removal(blank_app,monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第2页',3:'第1页'})
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    blank_app.renderers[str(d.path)]=PageRenderer(d.path)
    blank_app.ws.documents=[d];blank_app.activate_document(0)
    blank_app.resolve_boundary(3,False,d)
    assert not d.pages[2].split_before and d.omitted_blanks=={2} and d.groups[0].pages==[1,3]
    assert 2 not in d.manual_separator_pages


def test_restore_blank_ui_preserves_manual_keep_after_reanalysis(blank_app,monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第1页',3:'第2页'})
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    blank_app.renderers[str(d.path)]=PageRenderer(d.path)
    blank_app.ws.documents=[d];blank_app.activate_document(0)
    assert '自动删除' in presentation.page_info(d,2)[0]
    assert '自动删除' in blank_app.thumbs._badges(d.pages[1])[0][0]
    blank_app.set_output_exclusion([2],False)
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    assert not d.omitted_blanks and d.groups[0].pages==[1,2,3]
    assert 2 in d.manual_separator_pages


def test_manual_blank_deletion_does_not_split_and_survives_reanalysis(blank_app,monkeypatch,make_scan,tmp_path):
    d,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第1页',3:'第2页'})
    d.manual_separator_pages={2}  # Earlier manual keep.
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    blank_app.renderers[str(d.path)]=PageRenderer(d.path)
    blank_app.ws.documents=[d];blank_app.activate_document(0)
    blank_app.set_output_exclusion([2],True)
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    assert not d.separators and not d.split_starts and d.groups[0].pages==[1,3]
    assert '人工确认删除' in presentation.page_info(d,2)[0]
    assert not exporter.business_integrity_issues(d)
    g=d.groups[0];g.title,g.unit='报告','教务处';g.title_confirmed=g.unit_confirmed=True
    report=exporter.export_documents([d],exporter.ExportOptions(out_dir=tmp_path/'manual'))
    assert len(report.succeeded)==1 and not report.coverage_issues
    pages=json.loads(report.manifest_path.read_text(encoding='utf-8'))['sources'][0]['pages']
    assert pages[1]['status']=='excluded_manual' and pages[2]['output_page']==2


def test_doubtful_blank_is_visible_and_kept_without_automatic_removal(make_scan):
    d=document(make_scan('maybe.pdf',['blank']))
    d.pages[0].blank=BlankResult('maybe',.5)
    d.analysis_complete=True
    grouping.auto_group(d)
    issues=presentation.issues([d])
    assert any(i.category=='空白页' and i.page==1 and '保留' in i.label for i in issues)
    assert d.groups[0].pages==[1] and not d.excluded_pages


def test_boundary_across_deleted_blank_blocks_both_files_and_automatic_naming(monkeypatch,make_scan):
    from scansplit import automation
    d=document(make_scan('across.pdf',['text','text','blank','text','text']))
    apply(d)
    d.pages[3].split_before=True
    grouping.auto_group(d)
    assert [g.pages for g in d.groups]==[[1,2],[4,5]]
    d.analysis_complete=True;d.pending_boundaries={4:'交界仍需确认'}
    pipeline.update_boundary_state(d)
    assert all(not g.boundary_confirmed for g in d.groups)
    assert all(not presentation.split_ready(d,g) for g in d.groups)
    assert not automation.name_ready(d,SimpleNamespace(available=True))


def test_partial_export_cannot_bypass_pending_boundary_across_deleted_page(make_scan,tmp_path):
    d=document(make_scan('partial.pdf',['text','text','blank','text','text']))
    apply(d);d.pages[3].split_before=True;grouping.auto_group(d)
    d.analysis_complete=True;d.pending_boundaries={4:'待确认'}
    for g in d.groups: g.title,g.unit='报告','教务处';g.boundary_confirmed=True
    report=exporter.export_documents([d],exporter.ExportOptions(out_dir=tmp_path/'partial',only={(0,0)},strict=False))
    assert not report.succeeded and report.coverage_issues


def test_verified_blank_and_distinct_reliable_first_page_can_split_without_pagination():
    a,b=feature(None,title='教学设备统计表',reliable=False),feature(None,title='资产盘点汇总表',reliable=False)
    b['reliable_first_page']=True
    assert jointsplit.blank_start(a,b,True)[0]=='split'
    assert jointsplit.blank_start(a,b,False) is None


def test_blank_first_page_rule_is_wired_to_pipeline(monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{})
    read=pipeline.autoname._ocr_page_rotation_aware
    def distinct(*args):
        lines,w,h,q=read(*args)
        if args[1]==2:
            lines[0].text='资产盘点汇总表'
        return lines,w,h,q
    monkeypatch.setattr(pipeline.autoname,'_ocr_page_rotation_aware',distinct)
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    assert [g.pages for g in d.groups]==[[1],[3]] and not d.pending_boundaries
    assert '可靠首页' in d.boundary_evidence[3]['reason']


def test_leading_and_trailing_verified_blank_need_no_boundary_confirmation(monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['blank','text','blank'],{2:'第1页'})
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    assert [g.pages for g in d.groups]==[[2]] and not d.pending_boundaries
    assert d.groups[0].boundary_confirmed


@pytest.mark.parametrize('mode',['same_form','weak_first','continuation','appendix','weak_number','continuous_number','conflict'])
def test_blank_rule_never_overrides_ambiguity_or_pagination(mode):
    a,b=feature(None,title='教学设备统计表',reliable=False),feature(None,title='资产盘点汇总表',reliable=False)
    b['reliable_first_page']=True
    if mode=='same_form': b['structure']['title_text']=a['structure']['title_text']
    elif mode=='weak_first': b['reliable_first_page']=False
    elif mode=='continuation': b['structure']['has_continuation']=True
    elif mode=='appendix': b['structure']['title_text']='附件：资产盘点汇总表'
    else:
        from scansplit.pagenum import PaginationEvidence
        b['pagination']=PaginationEvidence(2,None,mode=='continuous_number','第2页' if mode!='conflict' else '第1页；第2页')
    assert jointsplit.blank_start(a,b,True) is None


def test_pending_boundary_reason_matches_actual_blank_deletion(monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['text','blank','text'],{1:'第2页'})
    pipeline.analyze_document(d,engine=engine,remove_blank=True)
    assert d.omitted_blanks=={2}
    assert '从输出删除' in d.pending_boundaries[3] and '候选（保留）' not in d.pending_boundaries[3]
    assert d.boundary_evidence[3]['reason']==d.pending_boundaries[3]
