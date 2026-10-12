"""Real offline OCR of synthetic, known groups; no business accuracy claims."""
import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from scansplit import autoname, exporter, ocrfields, unit_region, verify
from scansplit.model import Group, ScanDocument
from scansplit.ocr import OcrEngine
from scansplit.render import PageRenderer
from tests.ocrimg import make_cn_document, _font
from tests.samplegen import _add_image_page


def run(out):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    engine=OcrEngine.get()
    assert engine.available,engine.error
    started=time.monotonic()
    cases=[("last_page","计算机科学与技术学院",30),
           ("single_page","教务处",30),
           ("small_signature","科研管理办公室",18),
           ("manual_rotation","教务处",30),
           ("stored_rotation","计算机学院",30),
           ("trailing_blank","人事处",30),
           ("explicit_priority","科研处",30),
           ("no_unit",None,30),
           ("body_noise",None,30)]
    results=[]
    for name,unit,size in cases:
        first=make_cn_document("教学设备统计表",["填报单位：教务处"] if name=="explicit_priority" else ["请按实际情况填写本表。"])
        last=make_cn_document("",["以上情况已经核实。"],unit=unit,body_size=size)
        images=[first,last]
        if name=="single_page":
            images=[make_cn_document("教学设备统计表",["请按实际情况填写本表。"],unit=unit)]
        if name in ("manual_rotation","stored_rotation"):
            images[-1]=last.rotate(-90,expand=True)
        if name=="trailing_blank":
            images.append(Image.new("RGB",first.size,"white"))
        if name=="body_noise":
            ImageDraw.Draw(images[-1]).text((800,1650),"请报送教务处",font=_font(30),fill="black")
        source=out/(name+".pdf")
        writer=PdfWriter()
        for image in images:
            _add_image_page(writer,image)
        if name=="stored_rotation": writer.pages[-1].rotate(270)
        with source.open("wb") as f: writer.write(f)
        before=verify.file_sha256(source)
        d=ScanDocument(source,PdfReader(source))
        d.analysis_complete=True  # Groups/directions are known fixtures, not split evaluation.
        d.groups=[Group(1,len(images))]
        if name=="manual_rotation": d.pages[-1].rotation=270
        if name=="trailing_blank":
            d.pages[-1].omitted_blank=True
            d.manual_separator_pages.add(len(images))
            d.groups[0].excluded_pages=(len(images),)
        g=d.groups[0]
        renderer=PageRenderer(source)
        try:
            last0=g.pages[-1]-1
            # Same PDF, old reporting rule: whole-page 1200px, explicit labels only.
            baseline=[]
            for p in g.pages:
                ls,w,h,_=autoname._ocr_page_rotation_aware(renderer,p-1,engine,1200,d.pages[p-1])
                baseline.extend(c.text for c in ocrfields.extract_reporting_unit(ls,w,h))
            results_for_group=autoname.auto_name_groups(d,renderer,engine,unit_mode="reporting")
            assert not results_for_group[0].error,results_for_group[0].error
            expected="教务处" if name=="explicit_priority" else unit or ""
            assert g.unit==expected,(name,g.unit,expected,g.unit_candidates)
            if name!="explicit_priority": assert not g.unit_confirmed
            if unit and name!="explicit_priority":
                assert g.unit_candidates[0]["source"]==unit_region.SOURCE
                assert g.unit_candidates[0]["page"]==last0+1
            d.naming_attempted=True
            blocked=bool(exporter.preflight([d],exporter.ExportOptions(out_dir=out/(name+"-output"))))
            assert name=="explicit_priority" or blocked
            output=[]
            if name=="last_page":
                g.title_confirmed=g.unit_confirmed=True  # Explicitly simulated final name approval.
                g.title_manual=g.unit_manual=True
                report=exporter.export_documents([d],exporter.ExportOptions(out_dir=out/(name+"-output")))
                assert len(report.succeeded)==1 and not report.failed and not report.coverage_issues
                output=[str(r.path) for r in report.succeeded]
            results.append(dict(case=name,pages=len(images),source_sha256=before,
                baseline_whole_page_reporting_candidates=baseline,
                title=g.title,expected_unit=expected,actual_unit=g.unit,
                automatically_confirmed=results_for_group[0].best_unit.source=="填报单位" and autoname.reporting_unit_auto_confirm(results_for_group[0]) if results_for_group[0].best_unit else False,
                candidates=g.unit_candidates,blocked_before_name_confirmation=blocked,
                exported=output,source_unchanged=verify.file_sha256(source)==before))
        finally:
            renderer.close()
        assert verify.file_sha256(source)==before
    result=dict(elapsed_seconds=round(time.monotonic()-started,2),scenarios=results,
        limitation="Synthetic known groups; orientation and trailing blank exclusion are fixture setup. Only final name approval is simulated for one export. Not real-business extraction accuracy or automatic splitting acceptance.")
    (out/"acceptance.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",default="work/acceptance/unit-region-1.16.0")
    run(parser.parse_args().out)
