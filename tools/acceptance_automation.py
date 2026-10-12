"""Same-input comparison against saved v1.13 results, with real offline OCR."""
from pathlib import Path
import argparse
import json
import shutil
import time
from PIL import Image
from pypdf import PdfReader
from scansplit import pipeline, exporter, verify
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from tests.ocrimg import make_cn_document


def run(source_folder,out):
    source_folder,out=Path(source_folder),Path(out)
    out.mkdir(parents=True,exist_ok=True)
    baseline=json.loads((source_folder/'acceptance.json').read_text(encoding='utf-8'))
    engine=OcrEngine.get()
    assert engine.available,engine.error
    results=[]
    started=time.monotonic()
    for old in baseline['scenarios']:
        name=old['scenario']
        source=out/(name+'.pdf')
        shutil.copy2(source_folder/(name+'.pdf'),source)
        before=verify.file_sha256(source)
        assert before==old['source_sha256'],'Use the exact baseline PDF, never a changed fixture'
        doc=ScanDocument(source,PdfReader(source))
        t=time.monotonic()
        pipeline.organize_document(doc,engine=engine,remove_blank=False)  # Preserve v1.14 acceptance policy.
        current=dict(groups=[(g.start,g.end) for g in doc.groups],
                     directions=[p.src_index for p in doc.pages if p.has_rotation_proposal],
                     pending=dict(doc.pending_boundaries),
                     names=[dict(title=g.title,unit=g.unit,title_confirmed=g.title_confirmed,
                                 unit_confirmed=g.unit_confirmed) for g in doc.groups],
                     naming_attempted=doc.naming_attempted,seconds=round(time.monotonic()-t,2))
        assert not doc.separators
        assert [p for g in doc.groups for p in g.pages]==list(range(1,doc.page_count+1))
        assert not current['directions'],'Constructed upright samples need no manual orientation'
        if name=='weak_footer':
            assert current['groups']==[(1,4)] and set(doc.pending_boundaries)=={2,3,4}
            assert not doc.naming_attempted and not doc.groups[0].boundary_confirmed
            assert exporter.preflight([doc],exporter.ExportOptions(out_dir=out/name))
            exported=[]
        else:
            assert current['groups']==[tuple(g) for g in old['expected_groups']]
            assert not doc.pending_boundaries and doc.naming_attempted
            assert all(g.title=='教学设备统计表' and g.unit=='教务处' for g in doc.groups)
            # Only final name approval is human-simulated; no boundary/direction changes.
            for g in doc.groups:
                g.title_confirmed=g.unit_confirmed=True
                g.title_manual=g.unit_manual=True
                g.title_edit_kind=g.unit_edit_kind='confirmed'
            report=exporter.export_documents([doc],exporter.ExportOptions(out_dir=out/name))
            assert len(report.succeeded)==len(doc.groups) and not report.failed and not report.coverage_issues
            assert report.manifest_path
            exported=[str(r.path) for r in report.succeeded]
        assert verify.file_sha256(source)==before
        results.append(dict(scenario=name,source_sha256=before,baseline_initial=old['initial_automatic'],
            current_automatic=current,boundary_evidence=doc.boundary_evidence,
            simulated_manual_boundary_corrections=0,simulated_manual_orientation_confirmations=0,
            final_name_approval_simulated=name!='weak_footer',all_pages_retained=True,
            source_unchanged=True,exported_files=exported))
    # Real OCR negative orientation check, not a fake engine or an assumed score.
    image=make_cn_document('教学设备统计表',body_lines=['填报单位：教务处','本表由有关部门核实后填写。'])
    orientations=[image.rotate(a,expand=True) for a in (0,90,180,270)]
    source=out/'orientation.pdf'
    orientations[0].save(source,'PDF',save_all=True,append_images=orientations[1:],resolution=144,quality=95)
    before=verify.file_sha256(source)
    doc=ScanDocument(source,PdfReader(source))
    pipeline.organize_document(doc,engine=engine,remove_blank=False)
    assert all(doc.pages[i].has_rotation_proposal for i in (1,2,3)), 'Never silently accept a rotated image'
    assert all(p.effective_rotation==0 and not p.is_rotation_manual for p in doc.pages)
    assert verify.file_sha256(source)==before
    result=dict(elapsed_seconds=round(time.monotonic()-started,2),scenarios=results,
        rotated_pages_pending=[p.src_index for p in doc.pages if p.has_rotation_proposal],
        nonzero_rotations_applied=0,
        limitation='Exact v1.13 synthetic PDFs, not real-business accuracy. Final name approval is simulated; direction and boundaries are fully automatic only in the three clear cases. Weak Chinese page numbers remain pending.')
    (out/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',default='work/acceptance/pagination-1.13.0')
    parser.add_argument('--out',default='work/acceptance/automation-1.14.0')
    args=parser.parse_args()
    run(args.baseline,args.out)
