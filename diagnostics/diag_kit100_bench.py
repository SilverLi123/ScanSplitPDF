# -*- coding: utf-8 -*-
"""v1.9 固定回归基准 runner：100 页级四套样本（A/B/C/D）。

真值来源：stress_v19/kit100/X_标准答案.json（机器可读）+ X_逐页答案.csv（权威逐页）。
只调用 scansplit 现有模块（与 GUI worker 完全一致的调用方式），不修改生产代码。

指标：
- 空白检测：P/R/F1（对照 separator_blank_pages_remove ∪ internal_blank_pages_keep）；
  内部空白误删（internal_blank_pages_keep 被判 blank=误删风险）；稀疏页不得判 blank。
- 自动拆分：文档数、精确匹配、漏拆（真值边界未检出）、误拆（多拆边界）。
- 命名：标题/单位自动准确率（严格相等）、文件名逐份比对（含重名去重后缀）。
- 旋转：recall / 误报 / 漏报。
- 导出：成功数、逐页映射核验（应保存页面集合）、源 SHA-256 不变、原始图像流一致性。
- 性能：分阶段耗时、总耗时、峰值 RSS（含子进程）。

用法：./.venv/Scripts/python.exe diag_kit100_bench.py [A|B|C|D|all]
输出：stress_v19/kit100/bench_<case>.json + 控制台摘要
"""

import csv
import json
import sys
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).parent))

from scansplit import autoname, blankdetect, exporter, grouping, loader, naming, splitctx
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from scansplit.render import PageRenderer
from scansplit.verify import file_sha256

KIT = Path("stress_v19/kit100")


def load_truth(case: str) -> dict:
    ans = json.loads((KIT / f"{case}_标准答案.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader(
        open(KIT / f"{case}_逐页答案.csv", encoding="utf-8-sig")))
    per_page = []
    for r in rows:
        per_page.append({
            "page": int(r["PDF页码"]),
            "category": r["页面类别"],              # content / separator / internal_blank ...
            "doc": int(r["所属文档"]) if r["所属文档"].strip() else None,
            "in_doc_page": int(r["文档内页码"]) if r["文档内页码"].strip() else None,
            "remove": r["删除分隔页"].strip() == "1",
            "keep": r["原始应保存"].strip() == "1",
            "rotation": int(r["应顺时针旋转角度"]),
            "title": r["预期文档标题"], "unit": r["预期单位"],
        })
    return {"answer": ans, "per_page": per_page,
            "categories": sorted({p["category"] for p in per_page})}


class MemMonitor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak = 0
        self._stop = threading.Event()
        self._proc = psutil.Process()

    def run(self):
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss
                for c in self._proc.children(recursive=True):
                    try:
                        rss += c.memory_info().rss
                    except psutil.Error:
                        pass
                self.peak = max(self.peak, rss)
            except psutil.Error:
                pass
            self._stop.wait(0.05)

    def stop(self):
        self._stop.set()
        self.join(1.0)


def run_case(case: str) -> dict:
    truth = load_truth(case)
    ans, per_page = truth["answer"], truth["per_page"]
    pdf = KIT / ans["filename"]
    out_dir = KIT / f"out_{case}"
    result = {"case": case, "scenario": ans["scenario"], "pdf": ans["filename"],
              "truth": {
                  "pages": ans["pages"], "docs": ans["documents"],
                  "sep_remove": ans["separator_blank_pages_remove"],
                  "internal_keep": ans["internal_blank_pages_keep"],
                  "rotation": {int(k): v for k, v in ans["rotation_clockwise_degrees"].items()},
                  "no_blank_boundary_after": ans["no_blank_boundary_after_page"],
              },
              "phases": {}}
    mon = MemMonitor()
    mon.start()
    t_all = time.perf_counter()

    def phase(name, fn):
        t0 = time.perf_counter()
        out = fn()
        result["phases"][name] = round(time.perf_counter() - t0, 2)
        return out

    reader, warnings = loader.open_document(pdf)
    doc = ScanDocument(pdf, reader, warnings)
    grouping.refresh_groups(doc)
    n = doc.page_count
    result["pages_detected"] = n
    renderer = PageRenderer(pdf)
    engine = OcrEngine.get()
    result["ocr_available"] = engine.available

    def has_text(i):
        try:
            return len((reader.pages[i].extract_text() or "").strip()) > 10
        except Exception:
            return False

    # 真值集合
    truth_sep = set(ans["separator_blank_pages_remove"])
    truth_internal = set(ans["internal_blank_pages_keep"])
    truth_blank_all = truth_sep | truth_internal
    truth_rot = {int(k): v for k, v in ans["rotation_clockwise_degrees"].items()}
    keep_pages = {p["page"] for p in per_page if p["keep"]}      # 必须保留在最终输出
    remove_pages = {p["page"] for p in per_page if p["remove"]}  # 预计删除

    # -- 1. 空白检测 -----------------------------------------------------------
    verdicts = phase("detect_blank", lambda: blankdetect.detect_document(
        renderer, n, blankdetect.DetectParams(), has_text))
    for page, r in zip(doc.pages, verdicts):
        page.blank = r
    doc.status = "analyzed"
    pred_blank = {p + 1 for p, v in enumerate(verdicts) if v.verdict == "blank"}
    pred_maybe = {p + 1 for p, v in enumerate(verdicts) if v.verdict == "maybe"}
    tp = len(pred_blank & truth_blank_all)
    fp = pred_blank - truth_blank_all
    fn = truth_blank_all - pred_blank
    prec = tp / (tp + len(fp)) if tp + len(fp) else None
    rec = tp / (tp + len(fn)) if tp + len(fn) else None
    result["blank"] = {
        "verdicts_noncontent": {str(p + 1): v.verdict for p, v in enumerate(verdicts)
                                if v.verdict != "content"},
        "pred_blank": sorted(pred_blank), "pred_maybe": sorted(pred_maybe),
        "tp": tp, "fp": sorted(fp), "fn": sorted(fn),
        "precision": round(prec, 3) if prec is not None else None,
        "recall": round(rec, 3) if rec is not None else None,
        # 内部空白页必须保留：被判 blank 即为"误删风险"
        "internal_blank_misjudged_blank": sorted(pred_blank & truth_internal),
        "internal_blank_kept_ok": sorted(truth_internal - pred_blank) == sorted(truth_internal),
    }

    # -- 2. 自动分组（与 UI"自动分组"一致：先上下文判定，再分组） ---------------
    def _group():
        try:
            evs = splitctx.judge_blank_context(doc, renderer, engine)
            splitctx.apply_context(doc, evs)
        except Exception:
            pass  # OCR 不可用等：空白候选一律保守保留
        grouping.auto_group(doc)
        return [(g.start, g.end) for g in doc.groups]

    auto_groups = phase("auto_group", _group)
    truth_docs = [(g["start"], g["end"]) for g in ans["document_ground_truth"]]
    truth_bounds = {s for s, _ in truth_docs if s > 1}
    auto_bounds = {s for s, _ in auto_groups if s > 1}
    result["group"] = {
        "auto": auto_groups, "truth": truth_docs,
        "auto_docs": len(auto_groups), "truth_docs": len(truth_docs),
        "exact_match": sorted(auto_groups) == sorted(truth_docs),
        "missed_boundaries": sorted(truth_bounds - auto_bounds),
        "false_boundaries": sorted(auto_bounds - truth_bounds),
    }

    # -- 3. OCR 命名 -----------------------------------------------------------
    names = phase("ocr_naming", lambda: autoname.auto_name_groups(doc, renderer, engine=engine))
    naming_out = []
    title_hit = unit_hit = 0
    for r in names:
        g = doc.groups[r.group_index]
        # 真值标题/单位：按组起始页（内容页）在逐页表里找
        gt = next((p for p in per_page if p["page"] == g.start), None)
        t_ok = bool(gt and g.title == gt["title"])
        u_ok = bool(gt and g.unit == gt["unit"])
        title_hit += t_ok
        unit_hit += u_ok
        naming_out.append({
            "group": [g.start, g.end],
            "title_used": g.title, "title_truth": gt["title"] if gt else None, "title_ok": t_ok,
            "unit_used": g.unit, "unit_truth": gt["unit"] if gt else None, "unit_ok": u_ok,
            "title_confirmed": g.title_confirmed, "unit_confirmed": g.unit_confirmed,
            "used_ocr": r.used_ocr, "error": r.error,
        })
    n_docs_auto = len(names)
    result["naming"] = {
        "items": naming_out,
        "auto_docs": n_docs_auto,
        "title_exact": title_hit, "unit_exact": unit_hit,
        "title_acc": round(title_hit / n_docs_auto, 3) if n_docs_auto else None,
        "unit_acc": round(unit_hit / n_docs_auto, 3) if n_docs_auto else None,
    }

    # -- 4. 方向分析 -----------------------------------------------------------
    cands = [p for p in range(1, n + 1) if p not in doc.separators]
    dres = phase("direction_analysis", lambda: autoname.analyze_directions(
        doc, renderer, cands, progress_cb=lambda i, nn: None))
    sugg = {r.page: r.suggested_angle for r in dres if r.suggested_angle not in (None, 0)}
    truth_rot_set = set(truth_rot)
    tp_r = len(set(sugg) & truth_rot_set)
    fp_r = sorted(set(sugg) - truth_rot_set)
    fn_r = sorted(truth_rot_set - set(sugg))
    # 角度是否正确（已检出中角度一致的比例）
    angle_ok = sum(1 for p, a in sugg.items() if truth_rot.get(p) == a)
    result["rotation"] = {
        "suggestions": {str(k): v for k, v in sorted(sugg.items())},
        "truth": {str(k): v for k, v in sorted(truth_rot.items())},
        "detected": tp_r, "total_truth": len(truth_rot),
        "recall": round(tp_r / len(truth_rot), 3) if truth_rot else None,
        "angle_correct": angle_ok,
        "angle_correct_rate": round(angle_ok / len(sugg), 3) if sugg else None,
        "false_positives": fp_r, "missed": fn_r,
    }

    renderer.close()

    # -- 5. 导出 + 校验 --------------------------------------------------------
    def _export():
        opts = exporter.ExportOptions(template=naming.DEFAULT_TEMPLATE,
                                      out_dir=out_dir, strict=False)
        return exporter.export_documents([doc], opts)

    report = phase("export", _export)
    succeeded = report.succeeded
    # build_filename 返回不含扩展名的干名，落盘时补 .pdf；比对时统一补全。
    files = sorted(f"{r.filename}.pdf" for r in succeeded)
    # 逐页映射：从导出件读取实际页面集合
    from pypdf import PdfReader
    mapping_ok = True
    mapping_detail = []
    for r in succeeded:
        try:
            got = len(PdfReader(str(r.path)).pages)
        except Exception as e:
            got = f"ERR:{e}"
            mapping_ok = False
        mapping_detail.append({"filename": r.filename, "expected_pages": len(r.pages),
                               "got_pages": got})
        if got != len(r.pages):
            mapping_ok = False
    # 全部应保留页面是否都出现在允许导出的页面集合里（内容页 + 内部空白）
    exported_pages = sorted({p for r in succeeded for p in r.pages})
    result["export"] = {
        "succeeded": len(succeeded), "failed": len(report.failed),
        "failures": [{"file": r.filename, "error": r.error} for r in report.failed],
        "files": files,
        "page_maps": sorted([r.filename, list(r.pages)] for r in succeeded),
        "coverage_issues": report.coverage_issues,
        "sha_unchanged": all(
            report.src_sha_before.get(k) == report.src_sha_after.get(k)
            for k in report.src_sha_before if k in report.src_sha_after),
        "page_count_mapping_ok": mapping_ok,
        "page_count_mapping": mapping_detail,
        "keep_pages_missing_from_export": sorted(keep_pages - set(exported_pages)),
        "removed_pages_leaked_into_export": sorted(remove_pages & set(exported_pages)),
        "verify_errors": [line for line in report.logs
                          if any(k in line for k in ("校验失败", "不一致", "验证失败"))],
    }

    # -- 6. 文件名比对（含重名去重后缀）------------------------------------------
    expected_files = sorted(g["expected_filename"] for g in ans["document_ground_truth"])
    got_files = sorted(files)
    result["filename_match"] = {
        "expected": expected_files, "got": got_files,
        "exact_set_match": expected_files == got_files,
        "missing": sorted(set(expected_files) - set(got_files)),
        "unexpected": sorted(set(got_files) - set(expected_files)),
    }

    result["total_seconds"] = round(time.perf_counter() - t_all, 2)
    mon.stop()
    result["peak_rss_mb"] = round(mon.peak / 1048576, 1)
    result["sha256_before"] = file_sha256(pdf)
    result["sha256_after"] = report.src_sha_after.get(str(pdf))

    def _jsonify(o):
        if is_dataclass(o):
            return asdict(o)
        if isinstance(o, Path):
            return str(o)
        return str(o)

    out_json = KIT / f"bench_{case}.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_jsonify),
                        encoding="utf-8")
    return result


def summarize(r: dict) -> dict:
    return {
        "case": r["case"], "pages": r["pages_detected"],
        "blank_p_r": [r["blank"]["precision"], r["blank"]["recall"]],
        "internal_blank_ok": r["blank"]["internal_blank_kept_ok"],
        "group_auto_vs_truth": f'{r["group"]["auto_docs"]}/{r["group"]["truth_docs"]}',
        "group_exact": r["group"]["exact_match"],
        "missed/false_bounds": [len(r["group"]["missed_boundaries"]),
                                len(r["group"]["false_boundaries"])],
        "title_acc": r["naming"]["title_acc"], "unit_acc": r["naming"]["unit_acc"],
        "rotation_recall": r["rotation"]["recall"],
        "rot_fp/fn": [len(r["rotation"]["false_positives"]), len(r["rotation"]["missed"])],
        "export_ok/fail": [r["export"]["succeeded"], r["export"]["failed"]],
        "mapping_ok": r["export"]["page_count_mapping_ok"],
        "filename_match": r["filename_match"]["exact_set_match"],
        "sha_unchanged": r["export"]["sha_unchanged"],
        "total_s": r["total_seconds"], "peak_rss_mb": r["peak_rss_mb"],
    }


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    cases = ["A", "B", "C", "D"] if which == "all" else [which]
    summary = []
    for case in cases:
        print(f"\n{'='*60}\n=== case {case} ===")
        r = run_case(case)
        s = summarize(r)
        summary.append(s)
        print(json.dumps(s, ensure_ascii=False, indent=2))
    if len(summary) > 1:
        (KIT / "bench_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nsummary -> {KIT / 'bench_summary.json'}")


if __name__ == "__main__":
    main()
