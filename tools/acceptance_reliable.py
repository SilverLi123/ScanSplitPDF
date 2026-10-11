"""Reproducible offline mixed-scan acceptance. Human corrections are explicitly simulated."""
from pathlib import Path
import json
import time
import argparse
from PIL import Image
from pypdf import PdfReader
from scansplit.model import ScanDocument
from scansplit import pipeline, grouping, autoname, exporter, verify
from scansplit.ocr import OcrEngine
from scansplit.render import PageRenderer
from tests.ocrimg import make_cn_document


def run(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    blank = lambda: Image.new("RGB", (1400, 1800), "white")
    def form(title, unit, number=None, extra=None):
        body = ([f"填报单位：{unit}"] if unit else []) + (extra or []) + [
            "项目名称 数量 负责人 备注", "本表由有关部门核实后填写。", "各项数据应与实际情况一致。"]
        return make_cn_document(title, body_lines=body, header="示例大学文件",
                                unit="示例办公室", page_no=number)
    images = [
        form("教学设备统计表", "教务处", 1),
        form("教学设备统计表（续表）", "教务处", 2),
        blank(),
        form("教学设备统计表（续表）", "教务处", 3),
        blank(),
        form("教学设备统计表", "科研处", 1).rotate(90, expand=True),
        form("教学设备统计表（续表）", "科研处", 2),
        form("资产盘点汇总表", "总务处"),
        blank(),
        form("工作情况说明", None, 1),
        form("人员登记表", "教务处", 1, ["填报单位：科研处"]),
    ]
    source = out / "混合扫描验收.pdf"
    images[0].save(source, "PDF", save_all=True, append_images=images[1:], resolution=144)
    doc = ScanDocument(source, PdfReader(str(source)))
    before = verify.file_sha256(source)
    started = time.monotonic()
    engine = OcrEngine.get()
    assert engine.available, engine.error
    pipeline.analyze_document(doc, engine=engine)
    initial = dict(groups=[(g.start,g.end) for g in doc.groups],
        pending=dict(doc.pending_boundaries),
        rotations=[dict(page=p.src_index, angle=p.rotation_proposal, score=p.rotation_proposal_confidence)
                   for p in doc.pages if p.has_rotation_proposal])
    assert sorted(p for g in doc.groups for p in g.pages) == list(range(1,12)), "自动分析丢页"
    # Simulated human verifies orientation against the constructed gold image.
    for p in doc.pages:
        p.user_override_rotation = 90 if p.src_index == 6 else 0
        p.rotation_proposal = None
    pipeline.analyze_document(doc, engine=engine)
    after_orientation = dict(groups=[(g.start,g.end) for g in doc.groups], pending=dict(doc.pending_boundaries))
    # Simulated human verifies all boundaries and deliberately excludes ONLY 5/9.
    gold = [(1,4), (6,7), (8,8), (10,10), (11,11)]
    for p in doc.pages:
        p.separator = p.src_index in {5,9}
        p.split_before = p.src_index in {6,8,10,11}
    doc.manual_separator_pages = {3,5,9}  # 3 explicitly retained, 5/9 excluded
    doc.manual_boundaries = {p: p in {6,8,10,11} for p in range(1,12)}
    doc.pending_boundaries.clear()
    grouping.refresh_groups(doc)
    pipeline.update_boundary_state(doc)
    assert [(g.start,g.end) for g in doc.groups] == gold
    renderer = PageRenderer(source)
    try:
        autoname.auto_name_groups(doc,renderer,engine=engine,unit_mode="reporting")
    finally:
        renderer.close()
    auto_names = [dict(title=g.title, unit=g.unit, title_accepted=g.title_confirmed,
                      unit_accepted=g.unit_confirmed, unit_candidates=g.unit_candidates) for g in doc.groups]
    assert not doc.groups[3].unit, "无填报单位标签时不得猜测落款"
    assert not doc.groups[4].unit_confirmed, "多个填报单位必须暴露冲突"
    expected_names = [("教学设备统计表","教务处"),("教学设备统计表","科研处"),
        ("资产盘点汇总表","总务处"),("工作情况说明","人工补录单位"),("人员登记表","人工裁决单位")]
    # Final human name confirmation, distinct from measured automatic suggestions.
    for g, (title,unit) in zip(doc.groups,expected_names):
        g.title, g.unit = title, unit
        g.title_manual = g.unit_manual = True
        g.title_confirmed = g.unit_confirmed = True
    report = exporter.export_documents([doc], exporter.ExportOptions(out_dir=out/"export"))
    assert len(report.succeeded) == 5 and not report.failed and not report.coverage_issues
    assert sorted(p for r in report.succeeded for p in r.pages) == [1,2,3,4,6,7,8,10,11]
    assert verify.file_sha256(source) == before
    assert report.manifest_path and report.manifest_path.exists()
    result = dict(elapsed_seconds=round(time.monotonic()-started,2), page_count=11,
        initial_automatic=initial, after_orientation=after_orientation,
        automatic_names=auto_names, simulated_human_confirmed_groups=gold,
        exclusions=[5,9], internal_blank_retained=3,
        exported_files=[str(r.path) for r in report.succeeded],
        source_sha256=before, source_unchanged=True, fidelity_checks_passed=True,
        manifest=str(report.manifest_path),
        limitation="Synthetic mixed scans; final correctness includes explicit simulated human decisions, not fully automatic accuracy.")
    (out/"acceptance.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",default="work/acceptance")
    run(parser.parse_args().out)
