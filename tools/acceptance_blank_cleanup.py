"""Real offline OCR: same-input blank cleanup, independent blank boundaries and fidelity."""
from pathlib import Path
import argparse
import json
import shutil
import time
from PIL import Image, ImageDraw
from pypdf import PdfReader
from scansplit import pipeline, exporter, verify
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from tests.ocrimg import make_cn_document


def run(baseline, out):
    baseline, out = Path(baseline), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    old = json.loads((baseline/'acceptance.json').read_text(encoding='utf-8'))
    engine = OcrEngine.get()
    assert engine.available, engine.error
    start=time.monotonic()
    results=[]
    expected=dict(no_blank=[[1,2],[3,4]], blank_restart=[[1,2],[4,5]], internal_blank=[[1,3]])
    for previous in old['scenarios']:
        name=previous['scenario']
        source=out/(name+'.pdf')
        shutil.copy2(baseline/(name+'.pdf'),source)
        before=verify.file_sha256(source)
        assert before==previous['source_sha256']
        doc=ScanDocument(source,PdfReader(source))
        pipeline.organize_document(doc,engine=engine)
        automatic=dict(groups=[g.pages for g in doc.groups], deleted=sorted(doc.omitted_blanks),
            pending=dict(doc.pending_boundaries),directions=[p.src_index for p in doc.pages if p.has_rotation_proposal],
            names=[dict(title=g.title,unit=g.unit,need_confirm=g.need_confirm) for g in doc.groups])
        assert not automatic['directions']
        assert sorted([p for g in doc.groups for p in g.pages]+list(doc.excluded_pages))==list(range(1,doc.page_count+1))
        exports=[]
        if name=='weak_footer':
            assert automatic['groups']==[[1,2,3,4]] and set(doc.pending_boundaries)=={2,3,4}
            assert not doc.naming_attempted and exporter.preflight([doc],exporter.ExportOptions(out_dir=out/name))
        else:
            assert automatic['groups']==expected[name] and not doc.pending_boundaries
            assert doc.omitted_blanks==({'blank_restart':{3},'internal_blank':{2}}.get(name,set()))
            assert all(g.title=='教学设备统计表' and g.unit=='教务处' for g in doc.groups)
            for g in doc.groups:
                g.title_confirmed=g.unit_confirmed=True  # Explicit final name approval, not boundary edits.
                g.title_manual=g.unit_manual=True
                g.title_edit_kind=g.unit_edit_kind='confirmed'
            report=exporter.export_documents([doc],exporter.ExportOptions(out_dir=out/name))
            assert len(report.succeeded)==len(doc.groups) and not report.failed and not report.coverage_issues
            for r in report.succeeded:
                assert not verify.verify_mapping(source,r.path,r.pages,rotations=[0]*len(r.pages))
            destinations=json.loads(report.manifest_path.read_text(encoding='utf-8'))['sources'][0]['pages']
            assert {p['source_page'] for p in destinations if p['status']=='excluded_blank_rule'}==doc.omitted_blanks
            assert all(p['output_page'] is None and p['output'] is None for p in destinations if p['status']=='excluded_blank_rule')
            exports=[dict(path=str(r.path),pages=r.pages) for r in report.succeeded]
        assert verify.file_sha256(source)==before
        results.append(dict(scenario=name,source_sha256=before,automatic=automatic,
            all_pages_accounted_once=True,source_unchanged=True,exports=exports,
            final_name_approval_simulated=name!='weak_footer',manual_direction_or_boundary_changes=0))
    # Blank + distinct first-page fields, no printed page numbers at all.
    titles=['教学设备统计表','安全检查报告']
    units=['教务处','科研处']
    images=[make_cn_document(t,body_lines=[f'填报单位：{u}','本表由有关部门核实后填写。',
        '本次填写事项请核对原始材料。','完成后由负责人签字确认。'],title_size=88,body_size=46) for t,u in zip(titles,units)]
    source=out/'blank_without_pagination.pdf'
    blank=Image.new('RGB',images[0].size,'white')
    images[0].save(source,'PDF',save_all=True,append_images=[blank,images[1]],resolution=144,quality=95)
    before=verify.file_sha256(source)
    doc=ScanDocument(source,PdfReader(source));pipeline.organize_document(doc,engine=engine)
    independent=dict(groups=[g.pages for g in doc.groups],deleted=sorted(doc.omitted_blanks),
        pending=dict(doc.pending_boundaries),directions=[p.src_index for p in doc.pages if p.has_rotation_proposal],
        boundary_evidence=doc.boundary_evidence,names=[dict(title=g.title,unit=g.unit,need_confirm=g.need_confirm) for g in doc.groups])
    assert doc.omitted_blanks=={2} and verify.file_sha256(source)==before
    # These actual Chinese fields score below the strong first-page gate. Keep
    # the unresolved boundary visible rather than counting an intended split as success.
    assert independent['groups']==[[1,3]] and set(independent['pending'])=={3} and not independent['directions']
    assert not doc.naming_attempted
    # An otherwise blank page with a mark only in the cropped-away footer must stay.
    image=Image.new('RGB',(1240,1754),'white')
    ImageDraw.Draw(image).rectangle((540,1720,700,1730),fill='black')
    source=out/'footer_only.pdf';image.save(source,'PDF')
    before=verify.file_sha256(source)
    doc=ScanDocument(source,PdfReader(source));pipeline.organize_document(doc,engine=engine)
    assert not doc.excluded_pages and [g.pages for g in doc.groups]==[[1]]
    assert verify.file_sha256(source)==before
    result=dict(elapsed_seconds=round(time.monotonic()-start,2),same_input_pages=16,
        additional_pages=4,scenarios=results,blank_without_pagination=independent,
        footer_only_content_retained=True,limitation='Synthetic real-OCR acceptance, not mixed-business accuracy. Name approval remains explicitly simulated for the five same-input exports. Weak Chinese pagination and weak first-page fields without pagination remain pending.')
    (out/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',default='work/acceptance/automation-1.14.0-final')
    parser.add_argument('--out',default='work/acceptance/blank-cleanup-1.15.0')
    args=parser.parse_args();run(args.baseline,args.out)
