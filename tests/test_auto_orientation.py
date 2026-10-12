"""Automatic orientation, audit provenance, and manual priority."""
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from scansplit import automation, pipeline, presentation, exporter, verify, workflow
from scansplit.autoname import DirectionResult
from scansplit.model import PageState, Group, ScanDocument
from tests.test_ui_smoke import ui_env
from tests.test_review_browser import open_review
from tests.test_jointsplit import run_document


def result(page=1, angle=90, confidence=.9):
    return DirectionResult(page, {}, angle, 'OCR 四方向比对建议', confidence)


@pytest.mark.parametrize('angle', [0,90,180,270])
def test_applies_absolute_suggestion_and_roundtrips_without_claiming_manual(angle):
    p=PageState(1, original_rotation=90)
    automation.apply_direction_result(p,result(angle=angle))
    assert p.effective_rotation==angle and p.is_rotation_automatic and not p.is_rotation_manual
    assert p.detected_confidence==.9 and p.detected_note
    assert not p.has_rotation_proposal
    assert not automation.apply_direction_result(p,result(angle=angle))
    restored=PageState.restore(p.snapshot())
    assert restored.snapshot()==p.snapshot() and restored.original_rotation==90


@pytest.mark.parametrize('angle',[None,45])
@pytest.mark.parametrize('existing',[None,270])
def test_failed_suggestion_keeps_current_direction_pending(angle,existing):
    p=PageState(1,detected_rotation=existing)
    before=p.effective_rotation
    automation.apply_direction_result(p,result(angle=angle))
    assert p.effective_rotation==before and p.detected_rotation==existing
    assert p.rotation_proposal==before and not p.is_rotation_manual


@pytest.mark.parametrize('confidence',[float('nan'),float('inf'),-1,2])
def test_invalid_confidence_never_applies_rotation(confidence):
    p=PageState(1)
    automation.apply_direction_result(p,result(confidence=confidence))
    assert p.effective_rotation==0 and p.has_rotation_proposal


def test_low_confidence_suggestion_is_applied_and_exposed_for_audit(make_scan):
    source=make_scan('low.pdf',['text'])
    d=ScanDocument(source,PdfReader(source))
    automation.apply_direction_result(d.pages[0],result(confidence=.4))
    text=presentation.page_info(d,1)[1]
    assert '自动建议已应用 90°' in text and '把握较低' in text and '人工' not in text
    assert d.pages[0].direction_review_required


def test_low_confidence_applied_angle_still_blocks_direct_and_partial_export(make_scan,tmp_path):
    source=make_scan('needs-audit.pdf',['text'])
    d=ScanDocument(source,PdfReader(source))
    d.analysis_complete=True
    d.groups=[Group(1,1,'测试报告','教务处')]
    automation.apply_direction_result(d.pages[0],result(confidence=.4))
    for only in (None,{(0,0)}):
        report=exporter.export_documents([d],exporter.ExportOptions(out_dir=tmp_path/'out',only=only))
        assert not report.succeeded and report.coverage_issues
    assert not presentation.split_ready(d,d.groups[0])
    assert workflow.next_step([d]).button=='复查自动回正'
    issues=presentation.issues([d])
    assert any(i.action=='direction' and '已自动回正' in i.label for i in issues)


def test_clearly_upright_existing_gate_has_no_extra_review_click():
    from tests.test_automation import upright
    p=PageState(1)
    automation.apply_direction_result(p,upright())
    assert p.is_rotation_automatic and p.effective_rotation==0 and not p.direction_review_required


@pytest.mark.parametrize('angle',[0,90,180,270])
def test_manual_direction_survives_conflicting_reanalysis(angle):
    p=PageState(1,user_override_rotation=angle)
    before=p.snapshot()
    assert not automation.apply_direction_result(p,result(angle=(angle+90)%360))
    assert p.snapshot()==before and p.effective_rotation==angle


def test_pipeline_corrects_before_boundary_ocr_and_preserves_manual(monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['text']*4,{})
    d.pages[3].rotation=270
    angles={1:90,2:180,3:0}
    calls=[]
    def directions(doc,renderer,pages,**kw):
        assert pages==[1,2,3]
        return [result(p,angles[p]) for p in pages]
    monkeypatch.setattr(pipeline.autoname,'analyze_directions',directions)
    read=pipeline.autoname._ocr_page_rotation_aware
    def rotated(renderer,index,engine,width,ps):
        calls.append((index+1,ps.effective_rotation))
        return read(renderer,index,engine,width,ps)
    monkeypatch.setattr(pipeline.autoname,'_ocr_page_rotation_aware',rotated)
    original=d.path.read_bytes()
    pipeline.analyze_document(d,engine=engine)
    assert calls==[(1,90),(2,180),(3,0),(4,270)]
    assert all(not p.has_rotation_proposal for p in d.pages)
    assert d.pages[3].is_rotation_manual and d.path.read_bytes()==original
    pipeline.analyze_document(d,engine=engine)
    assert [p.effective_rotation for p in d.pages]==[90,180,0,270]


def test_missing_result_cannot_silently_certify_direction(monkeypatch,make_scan):
    d,engine=run_document(monkeypatch,make_scan,['text'],{})
    monkeypatch.setattr(pipeline.autoname,'analyze_directions',lambda *a,**kw:[])
    pipeline.analyze_document(d,engine=engine)
    assert d.pages[0].has_rotation_proposal and not d.groups[0].boundary_confirmed
    assert workflow.next_step([d]).button=='检查页面方向'


@pytest.mark.parametrize('failure',['cancel','error'])
def test_cancel_or_failure_rolls_back_applied_directions(monkeypatch,make_scan,failure):
    d,engine=run_document(monkeypatch,make_scan,['text'],{})
    before=d.snapshot()
    canceled=[False]
    def analyze(doc,*a,**kw):
        automation.apply_direction_result(doc.pages[0],result())
        if failure=='error': raise RuntimeError('expected')
        canceled[0]=True
    monkeypatch.setattr(pipeline,'analyze_document',analyze)
    if failure=='error':
        with pytest.raises(RuntimeError): pipeline.organize_document(d,engine=engine)
    else:
        assert not pipeline.organize_document(d,engine=engine,cancel=lambda:canceled[0])
    assert d.snapshot()==before


def test_export_adds_original_rotation_once_and_keeps_streams_and_source(make_scan,tmp_path):
    source=make_scan('original.pdf',['text']*4)
    writer=PdfWriter()
    for p in PdfReader(source).pages:
        p[NameObject('/Rotate')]=NumberObject(90)
        writer.add_page(p)
    path=tmp_path/'stored-rotation.pdf'
    with path.open('wb') as stream: writer.write(stream)
    d=ScanDocument(path,PdfReader(path))
    before=path.read_bytes()
    for p,a in zip(d.pages,[0,90,180,270]): automation.apply_direction_result(p,result(p.src_index,a))
    d.groups=[Group(1,4,'测试报告','教务处')]
    d.analysis_complete=True
    report=exporter.export_documents([d],exporter.ExportOptions(out_dir=tmp_path/'out'))
    assert len(report.succeeded)==1 and not report.coverage_issues
    exported=PdfReader(report.succeeded[0].path)
    assert [p.rotation for p in exported.pages]==[90,180,270,0]
    for original,output in zip(d.reader.pages,exported.pages):
        assert verify._contents_bytes(original)==verify._contents_bytes(output)
    assert path.read_bytes()==before


@pytest.mark.parametrize('confidence',[.4,.9])
def test_audit_is_readonly_and_batch_confirmation_keeps_boundaries_pending(ui_env,make_scan,confidence):
    root,app,d,dialog=open_review(ui_env,make_scan,3)
    try:
        d.pending_boundaries={2:'必须确认交界'}
        d.groups[0].boundary_confirmed=False
        for p,a in zip(d.pages,[90,0,270]): automation.apply_direction_result(p,result(p.src_index,a,confidence))
        dialog.audit_directions()
        before=d.snapshot()
        assert dialog.mode=='direction_audit' and dialog.browser_pages==[1,2,3]
        dialog.browse(1)
        assert dialog.browser_number==2 and dialog.page()==2
        assert dialog.left_page.page_no==2 and dialog.right_page.page_no==2
        assert '原第 2 页' in dialog.question.cget('text') and d.snapshot()==before
        dialog.confirm_audit()
        assert all(p.is_rotation_manual for p in d.pages)
        assert not d.analysis_stale and d.pending_boundaries=={2:'必须确认交界'}
        assert not d.groups[0].boundary_confirmed and dialog.mode=='boundary'
        dialog.undo()
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_correction_targets_browsed_page_and_invalidates_old_evidence(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan,3)
    try:
        for p in d.pages: automation.apply_direction_result(p,result(p.src_index,90))
        d.groups[0].title='人工标题'
        d.groups[0].title_manual=True
        before=d.snapshot()
        dialog.audit_directions()
        dialog.browse_at(1)
        dialog.correct_audit(90)
        assert d.pages[1].effective_rotation==180 and d.pages[1].is_rotation_manual
        assert d.pages[0].effective_rotation==90 and not d.pages[0].is_rotation_manual
        assert d.analysis_stale and not d.groups[0].boundary_confirmed
        assert d.groups[0].title=='人工标题' and d.groups[0].title_manual
        dialog.undo()
        assert d.snapshot()==before
    finally:
        dialog.close()
        app.on_close()


def test_advanced_direction_action_has_no_confirmation_popup(ui_env,make_scan,monkeypatch):
    root,app,d,dialog=open_review(ui_env,make_scan,2)
    try:
        def forbidden(*a,**kw): raise AssertionError('approval popup')
        monkeypatch.setattr(ui_env.messagebox,'askyesno',forbidden)
        app._review_dialog=dialog
        app._direction_show_results(d,[1,2],{},[result(1,90),result(2,0)])
        assert d.pages[0].is_rotation_automatic and d.pages[0].effective_rotation==90
        assert not any(p.has_rotation_proposal for p in d.pages)
        assert dialog.mode=='direction_audit' and not d.pages[0].is_rotation_manual
    finally:
        dialog.close()
        app.on_close()


def test_direction_batch_does_not_certify_unconfirmed_file_range(ui_env,make_scan):
    root,app,d,dialog=open_review(ui_env,make_scan,2)
    try:
        d.groups[0].boundary_confirmed=False
        for p in d.pages:
            automation.apply_direction_result(p,result(p.src_index,90,.4))
        dialog.refresh()
        assert dialog.mode=='direction_audit'
        dialog.confirm_audit()
        assert all(p.is_rotation_manual and not p.direction_review_required for p in d.pages)
        assert not d.groups[0].boundary_confirmed and dialog.mode=='groups'
        assert workflow.next_step([d]).action=='review'
    finally:
        dialog.close()
        app.on_close()
