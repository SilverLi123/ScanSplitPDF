"""Real offline OCR and automatic boundary acceptance on synthetic image PDFs.

Orientation is explicitly confirmed against constructed upright images; boundaries
are never corrected. This measures the new rules, not real-business OCR accuracy.
"""
from pathlib import Path
import argparse
import json
import time
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from scansplit import pipeline, exporter, verify
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from tests.ocrimg import make_cn_document


def run(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    engine = OcrEngine.get()
    assert engine.available, engine.error
    scenarios = [
        ("no_blank", [1, 2, 1, 2], [(1, 2), (3, 4)]),
        ("blank_restart", [1, 2, None, 1, 2], [(1, 3), (4, 5)]),
        ("internal_blank", [1, None, 2], [(1, 3)]),
        ("weak_footer", [1, 2, 1, 2], None),
    ]
    results = []
    started = time.monotonic()
    for name, numbers, expected in scenarios:
        images = []
        for n in numbers:
            if n is None:
                images.append(Image.new("RGB", (1400, 1800), "white"))
                continue
            image = make_cn_document("教学设备统计表", body_lines=["填报单位：教务处",
                      "本表由有关部门核实后填写。"], page_no=n if name == "weak_footer" else None)
            if name != "weak_footer":
                draw = ImageDraw.Draw(image)
                font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 40)
                label = f"Page {n} of 2"
                draw.text(((image.width-draw.textlength(label,font=font))/2,1700),label,font=font,fill="black")
            images.append(image)
        source = out / (name + ".pdf")
        images[0].save(source, "PDF", save_all=True, append_images=images[1:], resolution=144,quality=95)
        original_hash = verify.file_sha256(source)
        doc = ScanDocument(source, PdfReader(str(source)))
        pipeline.analyze_document(doc, engine=engine)
        initial = dict(groups=[(g.start, g.end) for g in doc.groups],
                       pending=dict(doc.pending_boundaries),
                       direction_proposals=[p.src_index for p in doc.pages if p.has_rotation_proposal])
        # Only orientation is simulated as human-confirmed, never the boundaries.
        for ps in doc.pages:
            if ps.blank.verdict != "blank":
                ps.user_override_rotation = 0
                ps.rotation_proposal = None
        pipeline.analyze_document(doc, engine=engine)
        actual = [(g.start, g.end) for g in doc.groups]
        if expected is None:
            assert actual == [(1,4)] and set(doc.pending_boundaries) == {2,3,4}
            assert all(not g.boundary_confirmed for g in doc.groups)
            for g in doc.groups:
                g.title,g.unit="验收报告","教务处"
                g.title_confirmed=g.unit_confirmed=True
            issues = exporter.preflight([doc], exporter.ExportOptions(out_dir=out/name))
            assert any("待确认" in issue for issue in issues) and verify.file_sha256(source) == original_hash
            results.append(dict(scenario=name, printed_numbers=numbers,initial_automatic=initial,
                after_orientation_groups=actual, pending_boundaries=doc.pending_boundaries,
                boundary_evidence=doc.boundary_evidence, export_blocked=True,
                all_pages_retained=True, source_unchanged=True,source_sha256=original_hash))
            continue
        assert actual == expected, (name, actual, expected, doc.boundary_evidence)
        assert not doc.pending_boundaries, (name, doc.pending_boundaries)
        assert not doc.separators and all(g.boundary_confirmed for g in doc.groups)
        assert [p for g in doc.groups for p in g.pages] == list(range(1, len(numbers)+1))
        # Naming is outside this acceptance's scope: explicitly confirmed values.
        for i, g in enumerate(doc.groups, 1):
            g.title, g.unit = f"验收报告{i}", "教务处"
            g.title_manual = g.unit_manual = True
            g.title_confirmed = g.unit_confirmed = True
        report = exporter.export_documents([doc], exporter.ExportOptions(out_dir=out/name))
        assert len(report.succeeded) == len(expected) and not report.failed and not report.coverage_issues
        assert report.manifest_path and verify.file_sha256(source) == original_hash
        results.append(dict(scenario=name, printed_numbers=numbers, initial_automatic=initial,
                            expected_groups=expected, automatically_split_after_orientation=actual,
                            boundary_evidence=doc.boundary_evidence,
                            all_pages_retained=True, source_sha256=original_hash,
                            source_unchanged=True, fidelity_checks_passed=True,
                            exported_files=[str(r.path) for r in report.succeeded],
                            manifest=str(report.manifest_path)))
    result = dict(elapsed_seconds=round(time.monotonic()-started,2), scenarios=results,
                  limitation="Synthetic image PDFs, real offline OCR. Orientation and names explicitly confirmed; zero manual boundary corrections. Not real-business accuracy.")
    (out/"acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="work/acceptance/pagination-1.13.0")
    run(parser.parse_args().out)
