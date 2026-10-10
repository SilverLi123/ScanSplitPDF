# -*- coding: utf-8 -*-
"""v1.9 任务2：81 页复杂扫描件压力测试（合成/真实样本通用 runner）。

只调用 scansplit 现有模块（与 GUI worker 完全一致的调用方式），不修改生产代码。
流程：导入 → 空白检测 → 自动分组 → OCR 命名 → 方向分析 → 导出+校验。
输出：stress_v19/stress_result.json + 控制台摘要。

用法：./.venv/Scripts/python.exe diag_v19_stress.py [pdf_path]
"""

import json
import sys
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).parent))

from scansplit import autoname, blankdetect, exporter, grouping, loader, naming
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from scansplit.render import PageRenderer
from scansplit.verify import file_sha256

PDF = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("stress_v19/stress81.pdf")
OUT_DIR = Path("stress_v19/out_auto")
RESULT_JSON = Path("stress_v19/stress_result.json")

# ---- 标准答案（人工逐页目检 81 页渲染图建立，见 stress_v19/sheets/） ----------
TRUTH_DOCS = [  # (start, end)
    (1, 5), (7, 10), (13, 19), (21, 24), (26, 31), (34, 38),
    (39, 42), (44, 51), (54, 60), (62, 66), (69, 75), (77, 80),
]
TRUTH_BLANK = {6, 11, 12, 20, 25, 32, 33, 43, 52, 53, 61, 67, 68, 76, 81}  # 分隔/末尾
TRUTH_INTERNAL_BLANK = {23, 58}   # 文档内部空白（不应作为拆分点）
TRUTH_SPARSE = {72}               # 内容稀疏+红章，绝不可判空白
TRUTH_ROTATED = set(range(13, 20)) | set(range(34, 39)) | {42} | (
    set(range(54, 61)) - {58})    # 19 页侧放


class MemMonitor(threading.Thread):
    """50ms 采样峰值 RSS（含原生内存，比 tracemalloc 更接近真实占用）。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak = 0
        self._stop = threading.Event()
        self._proc = psutil.Process()

    def run(self):
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss
                # 含子进程（OCR 引擎若独立进程）
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


def main():
    result = {"pdf": PDF.name, "sha256_before": file_sha256(PDF), "phases": {}}
    mon = MemMonitor()
    mon.start()
    t_all = time.perf_counter()

    def phase(name, fn):
        t0 = time.perf_counter()
        out = fn()
        result["phases"][name] = round(time.perf_counter() - t0, 2)
        print(f"[phase] {name}: {result['phases'][name]}s")
        return out

    # -- 导入 ------------------------------------------------------------------
    reader, warnings = loader.open_document(PDF)
    doc = ScanDocument(PDF, reader, warnings)
    grouping.refresh_groups(doc)
    n_pages = doc.page_count
    result["pages"] = n_pages

    renderer = PageRenderer(PDF)
    engine = OcrEngine.get()
    result["ocr_available"] = engine.available

    def has_text(i):
        try:
            return len((reader.pages[i].extract_text() or "").strip()) > 10
        except Exception:
            return False

    # -- 空白检测 ---------------------------------------------------------------
    def _detect():
        return blankdetect.detect_document(
            renderer, n_pages, blankdetect.DetectParams(), has_text)

    verdicts = phase("detect_blank", _detect)
    # 与 GUI on_done 一致：把结果写回页面状态，auto_group 才能读到
    for page, r in zip(doc.pages, verdicts):
        page.blank = r
    doc.status = "analyzed"
    result["blank_verdicts"] = {
        str(p + 1): v.verdict for p, v in enumerate(verdicts) if v.verdict != "content"
    }
    # 空白检测指标
    truth_blank_all = TRUTH_BLANK | TRUTH_INTERNAL_BLANK
    pred_blank = {p + 1 for p, v in enumerate(verdicts) if v.verdict == "blank"}
    pred_maybe = {p + 1 for p, v in enumerate(verdicts) if v.verdict == "maybe"}
    tp = len(pred_blank & truth_blank_all)
    fp_set = pred_blank - truth_blank_all
    fn_set = truth_blank_all - pred_blank
    result["blank_metrics"] = {
        "pred_blank": sorted(pred_blank), "pred_maybe": sorted(pred_maybe),
        "tp": tp, "fp": sorted(fp_set), "fn": sorted(fn_set),
        "precision": round(tp / (tp + len(fp_set)), 3) if tp + len(fp_set) else None,
        "recall": round(tp / (tp + len(fn_set)), 3) if tp + len(fn_set) else None,
        "sparse_p72_verdict": verdicts[71].verdict,  # 不得为 blank
        "internal_blank_as_split_risk": sorted(pred_blank & TRUTH_INTERNAL_BLANK),
    }

    # -- 自动分组 ---------------------------------------------------------------
    def _group():
        grouping.auto_group(doc)
        return [(g.start, g.end) for g in doc.groups]

    auto_groups = phase("auto_group", _group)
    result["auto_groups"] = auto_groups
    result["group_metrics"] = {
        "truth_docs": len(TRUTH_DOCS), "auto_docs": len(auto_groups),
        "groups_exact_match": sorted(auto_groups) == sorted(TRUTH_DOCS),
        "missed_boundaries": [],  # 漏拆：真值边界未检出
        "false_boundaries": [],   # 误拆：多拆出的边界
    }
    truth_bounds = {s for s, _ in TRUTH_DOCS if s > 1}
    auto_bounds = {s for s, _ in auto_groups if s > 1}
    result["group_metrics"]["missed_boundaries"] = sorted(truth_bounds - auto_bounds)
    result["group_metrics"]["false_boundaries"] = sorted(auto_bounds - truth_bounds)

    # -- OCR 命名 ----------------------------------------------------------------
    names = []

    def _name():
        return autoname.auto_name_groups(doc, renderer, engine=engine)

    names = phase("ocr_naming", _name)
    result["naming"] = []
    for r in names:
        g = doc.groups[r.group_index]
        result["naming"].append({
            "group": (g.start, g.end),
            "title_used": g.title, "unit_used": g.unit,
            "title_confirmed": g.title_confirmed, "unit_confirmed": g.unit_confirmed,
            "title_candidates": r.title_candidates[:3], "unit_candidates": r.unit_candidates[:3],
            "used_ocr": r.used_ocr, "error": r.error,
        })

    # -- 方向分析（全部非分隔页，与 GUI direction_analyze 一致）-------------------
    def _direction():
        cands = [p for p in range(1, n_pages + 1) if p not in doc.separators]
        return cands, autoname.analyze_directions(
            doc, renderer, cands, progress_cb=lambda i, n: None)

    cands, dres = phase("direction_analysis", _direction)
    sugg = {r.page: r.suggested_angle for r in dres if r.suggested_angle not in (None, 0)}
    result["rotation_suggestions"] = {str(k): v for k, v in sorted(sugg.items())}
    tp_rot = len(set(sugg) & TRUTH_ROTATED)
    fp_rot = sorted(set(sugg) - TRUTH_ROTATED)
    fn_rot = sorted(TRUTH_ROTATED - set(sugg))
    result["rotation_metrics"] = {
        "truth_rotated": sorted(TRUTH_ROTATED),
        "detected": tp_rot, "total_truth": len(TRUTH_ROTATED),
        "recall": round(tp_rot / len(TRUTH_ROTATED), 3),
        "false_positives": fp_rot, "missed": fn_rot,
    }

    renderer.close()

    # -- 导出（独立临时目录；压测聚焦保真，strict=False 避免 headless 无法确认）--
    def _export():
        opts = exporter.ExportOptions(
            template=naming.DEFAULT_TEMPLATE, out_dir=OUT_DIR, strict=False)
        return exporter.export_documents([doc], opts)

    report = phase("export", _export)
    result["export"] = {
        "succeeded": len(report.succeeded), "failed": len(report.failed),
        "failures": [
            {"doc": r.doc_name, "file": r.filename, "error": r.error}
            for r in report.failed
        ],
        "coverage_issues": report.coverage_issues,
        "sha_unchanged": all(
            report.src_sha_before.get(k) == report.src_sha_after.get(k)
            for k in report.src_sha_before if k in report.src_sha_after),
        "sha256_after": report.src_sha_after.get(str(PDF)),
    }
    result["verify_errors"] = [
        line for line in report.logs
        if any(k in line for k in ("校验失败", "不一致", "验证失败", "fail", "mismatch"))
    ]

    result["total_seconds"] = round(time.perf_counter() - t_all, 2)
    mon.stop()
    result["peak_rss_mb"] = round(mon.peak / 1048576, 1)

    def _jsonify(o):
        if is_dataclass(o):
            return asdict(o)
        return str(o)

    RESULT_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=_jsonify),
        encoding="utf-8")
    print(json.dumps({k: result[k] for k in (
        "blank_metrics", "group_metrics", "rotation_metrics", "export",
        "total_seconds", "peak_rss_mb")}, ensure_ascii=False, indent=2,
        default=_jsonify))
    print(f"\nfull result -> {RESULT_JSON}")


if __name__ == "__main__":
    main()
