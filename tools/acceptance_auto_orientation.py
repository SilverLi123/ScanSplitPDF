"""Known-angle synthetic scans: actual offline analysis, audit UI and export."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time
import tkinter as tk
from PIL import ImageGrab
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from scansplit import pipeline, settings, verify, exporter, automation
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from scansplit.ui import App
from scansplit.review_ui import ReviewDialog
from scansplit.main import _enable_dpi_awareness
from tests.ocrimg import make_cn_document
from tests.samplegen import _add_image_page


def run(out):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    source=out/'synthetic-orientation.pdf'
    writer=PdfWriter()
    for page,cw in enumerate([0,90,180,270,0],1):
        image=make_cn_document('教学设备统计表',[
            '填报单位：教务处', '项目名称 数量 负责人 备注',
            '本报告仅用于离线方向回正验收，所有数据均为虚构。',
            '请按实际页面内容核对，源扫描文件保持原样。',
            '实验设备已经完成登记，后续工作依照安排开展。',
            '扫描整理时需保持页码连续并检查文件完整性。'],page_no=page)
        _add_image_page(writer,image.rotate(-cw,expand=True))
        if page==5:
            writer.pages[-1][NameObject('/Rotate')]=NumberObject(90)
    with source.open('wb') as stream: writer.write(stream)
    doc=ScanDocument(source.resolve(),PdfReader(source))
    before=verify.file_sha256(source)
    started=time.monotonic()
    assert pipeline.organize_document(doc,engine=OcrEngine.get())
    expected=[0,270,180,90,270]
    actual=[p.effective_rotation for p in doc.pages]
    assert actual==expected,(expected,actual)
    assert all(p.is_rotation_automatic and not p.has_rotation_proposal for p in doc.pages)
    first_pending=dict(doc.pending_boundaries)
    first_direction_reviews=[p.src_index for p in doc.pages if p.direction_review_required]
    # This orientation acceptance does not certify ambiguous boundaries.
    # Simulate the human checking the known five-page document's memberships.
    for p in doc.pending_boundaries:
        doc.manual_boundaries[p]=False
    doc.pending_boundaries.clear()
    for p in doc.pages:
        p.rotation=p.effective_rotation
    pipeline.update_boundary_state(doc)
    automation.name_ready(doc,OcrEngine.get())
    assert len(doc.groups)==1 and doc.groups[0].title=='教学设备统计表' and doc.groups[0].unit=='教务处'
    assert pipeline.organize_document(doc,engine=OcrEngine.get())
    assert [p.effective_rotation for p in doc.pages]==expected
    seconds=time.monotonic()-started
    # Restore automatic provenance for the UI batch-review demonstration.
    for p in doc.pages:
        p.user_override_rotation=None
        p.direction_review_required=p.src_index in first_direction_reviews
    pipeline.update_boundary_state(doc)

    _enable_dpi_awareness()
    settings.load_settings=lambda:{}
    settings.save_settings=lambda _:None
    root=tk.Tk()
    app=App(root)
    app.import_files([str(source.resolve())])
    active=app._active_doc()
    active.restore(doc.snapshot())
    app._refresh_all()
    dialog=ReviewDialog(root,app,active)
    app._review_dialog=dialog
    captures=[]
    def shot(name):
        dialog.lift()
        dialog.attributes('-topmost',True)
        for _ in range(4):
            root.update()
            time.sleep(.1)
        api=ctypes.WinDLL('user32',use_last_error=True)
        api.GetParent.argtypes=[wintypes.HWND]
        api.GetParent.restype=wintypes.HWND
        ImageGrab.grab(window=api.GetParent(dialog.winfo_id())).save(out/name)
        assert dialog.actions.winfo_y()+dialog.actions.winfo_height()<=dialog.winfo_height()
        captures.append(dict(file=name,mode=dialog.mode,browsed_page=dialog.browser_number,
                             geometry=dialog.geometry()))
    try:
        dialog.audit_directions()
        dialog.browse_at(1)
        shot('01-auto-audit-maximized.png')
        dialog.state('normal')
        dialog.geometry('1366x700+20+20')
        dialog.notebook.select(dialog.comparison)
        shot('02-original-corrected-comparison.png')
        snap=active.snapshot()
        dialog.browse_at(4)
        shot('03-stored-pdf-rotation.png')
        assert active.snapshot()==snap
        dialog.confirm_audit()
        assert all(p.is_rotation_manual for p in active.pages)
        assert not active.analysis_stale
        shot('04-back-to-file-review.png')
        app.confirm_group_boundary(active,0)  # Separate explicit file-membership approval.
        # Explicit final human naming approval; records are separate from direction approval.
        active.groups[0].title_confirmed=True
        active.groups[0].unit_confirmed=True
        report=exporter.export_documents([active],exporter.ExportOptions(out_dir=out/'export'))
        assert len(report.succeeded)==1 and not report.failed and not report.coverage_issues
        exported=PdfReader(report.succeeded[0].path)
        assert [p.rotation for p in exported.pages]==[0,270,180,90,0]
        assert verify.file_sha256(source)==before
    finally:
        dialog.close()
        app.on_close()
    data=dict(expected_extra_angles=expected,actual_extra_angles=actual,original_pdf_rotation_page_5=90,
              repeated_analysis_idempotent=True,analysis_twice_seconds=round(seconds,2),
              first_pending_boundaries=first_pending,first_direction_reviews=first_direction_reviews,
              simulated_human_boundary_approval=list(first_pending),
              group_ranges_after_membership_approval=[(g.start,g.end) for g in doc.groups],
              automatic_title=doc.groups[0].title,automatic_unit=doc.groups[0].unit,
              direction_confidences=[p.detected_confidence for p in doc.pages],
              batch_direction_confirmation=True,export_verified=True,source_sha256=before,
              source_unchanged=True,captures=captures,
              scope='Synthetic known-angle scans, actual local OCR; not a real business accuracy estimate.')
    (out/'orientation-check.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data,ensure_ascii=True,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True)
    run(parser.parse_args().out)
