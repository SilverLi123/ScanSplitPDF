"""Last-page regional evidence, provenance, cancellation and manual priority."""
import pytest
from PIL import Image
from pypdf import PdfReader

from scansplit import autoname, exporter, ocr_cache, presentation, unit_region
from scansplit.model import BlankResult, Group, PageState, ScanDocument
from scansplit.ocr import OcrLine
from scansplit.ocrfields import FieldCandidate
from tests.test_ui_smoke import _make_root, ui_env


def line(text, x=700, y=1180, score=.98, width=180):
    return OcrLine(text, score, [(x,y),(x+width,y),(x+width,y+30),(x,y+30)])


def test_bottom_right_standalone_unit_is_only_a_candidate():
    found=unit_region.extract([line("计算机学院")],1000,1400,7)
    assert len(found)==1 and found[0].text=="计算机学院"
    c=found[0]
    assert c.source==unit_region.SOURCE and c.page==7
    assert c.bbox==[700,1180,880,1210] and c.image_size==[1000,1400]
    assert not autoname.reporting_unit_auto_confirm(autoname.NameResult(0,unit_candidates=found))


@pytest.mark.parametrize("name",["经济管理学院","对外合作办公室","联合研究院"])
def test_organisation_prefix_not_mistaken_for_sentence(name):
    assert unit_region.extract([line(name)],1000,1400,1)[0].text==name


@pytest.mark.parametrize("text,x,y,score",[
    ("教务处",50,1180,.98), ("教务处",700,400,.98),
    ("教务处",700,1180,.59), ("会议室",700,1180,.98),
    ("经教务处",700,1180,.98), ("请报送教务处",700,1180,.98),
    ("为进一步完善各部",700,1180,.98), ("2026年10月12日",700,1180,.98),
    ("第2页",700,1180,.98), ("联系电话123室",700,1180,.98),
    ("教务处",700,1180,float("nan")), ("教务处",700,1180,float("inf")),
])
def test_non_signature_noise_is_not_a_unit(text,x,y,score):
    assert not unit_region.extract([line(text,x,y,score)],1000,1400,1)


def test_nearby_other_role_label_prevents_signature_fallback():
    assert not unit_region.extract([line("使用单位：",450,1180),line("教务处")],1000,1400,1)


def test_explicit_label_beats_higher_scoring_signature_and_retains_source():
    primary=[FieldCandidate("教务处",.86,"填报单位",page=1)]
    signature=unit_region.extract([line("科研处")],1000,1400,3)
    found=unit_region.merge(primary,signature)
    assert [c.text for c in found]==["教务处","科研处"]
    assert autoname.reporting_unit_auto_confirm(autoname.NameResult(0,unit_candidates=found))
    conflict=unit_region.extract([line("填报单位：科研处",450,1180,width=450)],1000,1400,3)
    found=unit_region.merge(primary,conflict)
    assert len(found)==2 and not autoname.reporting_unit_auto_confirm(autoname.NameResult(0,unit_candidates=found))


def test_same_text_prefers_explicit_evidence_not_signature():
    found=unit_region.merge([FieldCandidate("教务处",.61,"填报单位",page=1)],
                           [FieldCandidate("教务处",.97,unit_region.SOURCE,page=3)])
    assert len(found)==1 and found[0].source=="填报单位" and found[0].confidence==.61


def test_two_signatures_stay_visible_and_unconfirmed():
    found=unit_region.extract([line("教务处"),line("科研处",y=1240)],1000,1400,3)
    assert {c.text for c in found}=={"教务处","科研处"}
    assert not autoname.reporting_unit_auto_confirm(autoname.NameResult(0,unit_candidates=found))


class Renderer:
    def __init__(self,path):
        self.path=path
        self.calls=[]
    def render(self,index,**kwargs):
        self.calls.append((index,kwargs))
        return Image.new("RGB",(1000,1400),"white")


class Engine:
    available=True
    def __init__(self,lines=None):
        self.lines=lines if lines is not None else [line("教务处",350,270)]
        self.calls=0
    def recognize(self,image):
        self.calls+=1
        assert image.size==(650,490)
        return self.lines


def test_crop_coordinates_rotation_and_cache_are_consistent(make_scan):
    path=make_scan("cache.pdf",["text"])
    before=path.read_bytes()
    r,e=Renderer(path),Engine()
    cache=ocr_cache.get_cache()
    cache.clear()
    state=PageState(1,user_override_rotation=270)
    found=unit_region.scan(r,0,e,state)
    assert found[0].bbox==[700,1180,880,1210]
    found[0].text="mutation"
    assert unit_region.scan(r,0,e,state)[0].text=="教务处" and e.calls==1
    assert all(kw["user_rotation"]==270 for _,kw in r.calls)
    state.user_override_rotation=0
    unit_region.scan(r,0,e,state)
    assert e.calls==2
    path.write_bytes(before+b"\n")
    unit_region.scan(r,0,e,state)
    assert e.calls==3  # Source signature invalidates the regional result.


def test_empty_region_not_cached_and_cancel_does_not_call_ocr(make_scan):
    r,e=Renderer(make_scan("empty.pdf",["text"])),Engine([])
    state=PageState(1)
    assert unit_region.scan(r,0,e,state)==[]
    assert unit_region.scan(r,0,e,state)==[] and e.calls==2
    assert unit_region.scan(r,0,e,state,cancel=lambda:True)==[] and e.calls==2


def run_naming(make_scan,monkeypatch,*,pages=3,primary=None):
    path=make_scan("naming.pdf",["text"]*pages)
    doc=ScanDocument(path,PdfReader(path))
    doc.groups=[Group(1,pages,"old title","old unit",unit_confirmed=False)]
    doc.analysis_complete=doc.naming_attempted=True
    def whole(renderer,index,engine,width,page_state):
        lines=[line("教学设备统计表",250,200,width=450)]
        if primary: lines.append(line("填报单位："+primary,80,500,width=400))
        return lines,1000,1400,Image.new("RGB",(1000,1400),"white")
    monkeypatch.setattr(autoname,"_ocr_page_rotation_aware",whole)
    monkeypatch.setattr(autoname,"_has_text_layer",lambda *args:False)
    return doc,Renderer(path),Engine()


@pytest.mark.parametrize("deleted",[False,True])
def test_uses_last_retained_content_page_not_trailing_blank_or_exclusion(make_scan,monkeypatch,deleted):
    d,r,e=run_naming(make_scan,monkeypatch)
    if deleted:
        d.groups[0].excluded_pages=(3,)
        d.pages[2].omitted_blank=True
    else:
        d.pages[2].blank=BlankResult("blank",1,{})
    before=d.path.read_bytes()
    result=autoname.auto_name_groups(d,r,e,unit_mode="reporting")
    assert r.calls[0][0]==1 and result[0].best_unit.page==2
    assert d.groups[0].unit=="教务处" and not d.groups[0].unit_confirmed
    assert d.path.read_bytes()==before
    issue=next(i for i in presentation.issues([d]) if i.field=="unit")
    assert issue.page==2 and "落款" in issue.reason
    assert not presentation.name_ready(d,d.groups[0])


@pytest.mark.parametrize("preserve",[False,True])
def test_manual_unit_survives_new_regional_analysis(make_scan,monkeypatch,preserve):
    d,r,e=run_naming(make_scan,monkeypatch)
    g=d.groups[0]
    g.unit="人工单位"
    g.unit_manual=g.unit_confirmed=True
    g.unit_edit_kind="edited"
    autoname.auto_name_groups(d,r,e,unit_mode="reporting",preserve_confirmed=preserve)
    assert g.unit=="人工单位" and g.unit_confirmed and g.unit_manual and g.unit_edit_kind=="edited"


def test_primary_reporting_label_keeps_priority_during_real_orchestration(make_scan,monkeypatch):
    d,r,e=run_naming(make_scan,monkeypatch,primary="科研处")
    autoname.auto_name_groups(d,r,e,unit_mode="reporting")
    assert d.groups[0].unit=="科研处" and d.groups[0].unit_confirmed
    assert {c["text"] for c in d.groups[0].unit_candidates}=={"科研处","教务处"}


def test_cancel_after_region_ocr_does_not_publish_partial_names(make_scan,monkeypatch):
    d,r,e=run_naming(make_scan,monkeypatch)
    before=d.groups[0].snapshot()
    assert not autoname.auto_name_groups(d,r,e,unit_mode="reporting",cancel_cb=lambda:e.calls>0)
    assert d.groups[0].snapshot()==before


def test_failed_region_cannot_automatically_confirm_unit(make_scan,monkeypatch):
    d,r,e=run_naming(make_scan,monkeypatch,primary="科研处")
    def fail(image): raise RuntimeError("regional OCR failed")
    e.recognize=fail
    result=autoname.auto_name_groups(d,r,e,unit_mode="reporting")
    assert "regional OCR failed" in result[0].error
    assert not d.groups[0].unit_confirmed


def test_signature_candidate_blocks_export_until_user_confirms(make_scan,monkeypatch,tmp_path):
    d,r,e=run_naming(make_scan,monkeypatch)
    autoname.auto_name_groups(d,r,e,unit_mode="reporting")
    assert exporter.preflight([d],exporter.ExportOptions(out_dir=tmp_path/"output"))


def test_ui_shows_signature_warning_and_locates_last_page_without_confirmation(ui_env,make_scan):
    root=_make_root()
    app=ui_env.App(root)
    try:
        app.import_files([str(make_scan("source-ui.pdf",["text"]*3))])
        d=app._active_doc()
        d.analysis_complete=d.naming_attempted=True
        g=d.groups[0]
        g.boundary_confirmed=True
        g.title="教学设备统计表"
        g.title_confirmed=True
        g.unit="教务处"
        g.unit_confirmed=False
        g.unit_candidates=[unit_region.extract([line("教务处")],1000,1400,3)[0].to_dict()]
        app._refresh_all()
        panel=app.groups.panels[0]
        assert "末页右下角落款" in panel.unit_conf_lbl.cget("text")
        assert "是否为填报单位" in panel.unit_conf_lbl.cget("text")
        panel.focus_pending("unit")
        assert app.current_page==3 and not g.unit_confirmed and not g.unit_manual
        panel._show_source("unit")
        assert app.current_page==3 and not g.unit_confirmed
        g.boundary_confirmed=False
        panel.focus_pending("unit")
        assert app.current_page==1 and not g.unit_confirmed
    finally:
        app.on_close()
