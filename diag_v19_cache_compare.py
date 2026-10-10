# -*- coding: utf-8 -*-
"""v1.9 任务 B：OCR 缓存三态对比（关闭 / 开启首次 / 开启重复）。

在同一 81 页样本上各跑一次完整流水线，逐项对比：
空白判定、分组、命名（标题/单位/候选明细）、方向 qualities+建议角、
导出文件名与逐页映射、源 SHA——缓存命中与未命中必须完全一致。
输出 stress_v19/cache_compare.json 与控制台结论。

用法：./.venv/Scripts/python.exe diag_v19_cache_compare.py
"""

import copy
import json
import sys
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).parent))

from scansplit import autoname, blankdetect, exporter, grouping, loader, naming
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine, OcrEngineNoCls
from scansplit.ocr_cache import get_cache
from scansplit.render import PageRenderer
from scansplit.verify import file_sha256

PDF = Path("stress_v19/stress81.pdf")
OUT = Path("stress_v19/cache_compare.json")


def run_once(tag: str, clear_cache: bool = True) -> dict:
    cache = get_cache()
    if clear_cache:
        cache.clear()
    cache.reset_stats()
    mon_rss = {"peak": 0}
    stop = threading.Event()
    proc = psutil.Process()

    def mon():
        while not stop.is_set():
            try:
                rss = proc.memory_info().rss
                for c in proc.children(recursive=True):
                    try:
                        rss += c.memory_info().rss
                    except psutil.Error:
                        pass
                mon_rss["peak"] = max(mon_rss["peak"], rss)
            except psutil.Error:
                pass
            stop.wait(0.05)

    t = threading.Thread(target=mon, daemon=True)
    t.start()
    t_all = time.perf_counter()
    phases = {}

    def phase(name, fn):
        t0 = time.perf_counter()
        out = fn()
        phases[name] = round(time.perf_counter() - t0, 2)
        return out

    reader, warnings = loader.open_document(PDF)
    doc = ScanDocument(PDF, reader, warnings)
    grouping.refresh_groups(doc)
    n = doc.page_count
    renderer = PageRenderer(PDF)
    engine = OcrEngine.get()

    def has_text(i):
        try:
            return len((reader.pages[i].extract_text() or "").strip()) > 10
        except Exception:
            return False

    verdicts = phase("detect", lambda: blankdetect.detect_document(
        renderer, n, blankdetect.DetectParams(), has_text))
    for page, r in zip(doc.pages, verdicts):
        page.blank = r
    doc.status = "analyzed"
    groups = phase("group", lambda: grouping.auto_group(doc))

    names = phase("naming", lambda: autoname.auto_name_groups(doc, renderer, engine=engine))
    naming_out = []
    for r in names:
        g = doc.groups[r.group_index]
        naming_out.append({
            "group": [g.start, g.end], "title": g.title, "unit": g.unit,
            "title_conf": g.title_conf, "unit_conf": g.unit_conf,
            "title_confirmed": g.title_confirmed, "unit_confirmed": g.unit_confirmed,
            "unit_cands": [c.to_dict() for c in r.unit_candidates],
        })

    cands = [p for p in range(1, n + 1) if p not in doc.separators]
    dres = phase("direction", lambda: autoname.analyze_directions(
        doc, renderer, cands, progress_cb=lambda i, nn: None))
    dir_out = [
        {"page": r.page, "qualities": r.qualities,
         "suggested": r.suggested_angle, "note": r.suggestion_note}
        for r in dres
    ]
    renderer.close()

    out_dir = Path(f"stress_v19/out_cache_{tag}")
    report = phase("export", lambda: exporter.export_documents([doc], exporter.ExportOptions(
        template=naming.DEFAULT_TEMPLATE, out_dir=out_dir, strict=False)))
    export_out = {
        "files": sorted(r.filename for r in report.succeeded),
        "page_maps": sorted([r.filename, r.pages] for r in report.succeeded),
        "failed": len(report.failed),
        "coverage": report.coverage_issues,
        "sha_ok": all(
            report.src_sha_before.get(k) == report.src_sha_after.get(k)
            for k in report.src_sha_before if k in report.src_sha_after),
    }
    stop.set()
    t.join(1.0)
    stats = copy.deepcopy(cache.stats())
    return {
        "tag": tag,
        "total_s": round(time.perf_counter() - t_all, 2),
        "phases": phases,
        "peak_rss_mb": round(mon_rss["peak"] / 1048576, 1),
        "cache": stats,
        "blank": {str(i + 1): v.verdict for i, v in enumerate(verdicts) if v.verdict != "content"},
        "groups": [[g.start, g.end] for g in groups],
        "naming": naming_out,
        "direction": dir_out,
        "export": export_out,
        "src_sha": file_sha256(PDF),
    }


def strip_perf(r: dict) -> dict:
    """只保留结果语义字段（剔除耗时/内存/统计），用于一致性比对。"""
    return {k: r[k] for k in (
        "blank", "groups", "naming", "direction", "export", "src_sha")}


def main():
    results = []
    for tag, enabled, desc, clear in (
        ("off", False, "缓存关闭", True),
        ("on1", True, "缓存开启·首次执行", True),
        ("on2", True, "缓存开启·重复执行", False),  # 保留 on1 的缓存条目
    ):
        get_cache().set_enabled(enabled)
        print(f"=== run {tag}（{desc}）===")
        r = run_once(tag, clear_cache=clear)
        results.append(r)
        print(json.dumps({k: r[k] for k in ("total_s", "phases", "peak_rss_mb", "cache")},
                         ensure_ascii=False))

    off, on1, on2 = (strip_perf(r) for r in results)
    same = {"off_vs_on1": off == on1, "on1_vs_on2": on1 == on2, "off_vs_on2": off == on2}
    if not all(same.values()):
        for a, b, la, lb in ((off, on1, "off", "on1"), (on1, on2, "on1", "on2")):
            if a != b:
                for k in a:
                    if a[k] != b[k]:
                        print(f"!! 差异 {la} vs {lb} 字段 {k}")
    print("一致性：", json.dumps(same, ensure_ascii=False))
    OUT.write_text(json.dumps(
        {"consistency": same, "runs": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"full -> {OUT}")


if __name__ == "__main__":
    main()
