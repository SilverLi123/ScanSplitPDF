# -*- coding: utf-8 -*-
"""阶段 A1 验收：四套 100 页样本 + stress81 的方向识别实际准确率。

对每个样本跑真实 analyze_directions，与标准答案 rotation_clockwise_degrees
比对，报告：方向准确率（命中/漏判/判错/误报），并列出问题页。

用法： ./.venv/Scripts/python.exe diag_a1_eval.py [A|B|C|D|E|all]
       （E = stress81）
"""
import json
import sys
import time

sys.path.insert(0, ".")

from scansplit import autoname, blankdetect, splitctx
from scansplit.loader import open_document
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from scansplit.render import PageRenderer

KIT = {
    "A": "A_标准空白分隔_100页_纯图片.pdf",
    "B": "B_双面灰底与噪点_104页_纯图片.pdf",
    "C": "C_连续多页侧放表格_108页_纯图片.pdf",
    "D": "D_复杂边界与命名_100页_纯图片.pdf",
}


def _run_kit(case: str) -> dict:
    base = f"stress_v19/kit100/{case}"
    ans = json.load(open(f"{base}_标准答案.json", encoding="utf-8"))
    truth = {int(k): int(v) for k, v in ans["rotation_clockwise_degrees"].items()}
    pdf = f"stress_v19/kit100/{KIT[case]}"
    reader, warns = open_document(pdf)
    doc = ScanDocument(pdf, reader, warns)
    renderer = PageRenderer(pdf)
    t0 = time.time()
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(), lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        # 业务属性判定（A0）后，分隔页不参与方向分析
        splitctx.apply_context(
            doc, splitctx.judge_blank_context(doc, renderer, OcrEngine.get()))
        candidates = [p for p in range(1, doc.page_count + 1)
                      if p not in doc.separators and (doc.pages[p - 1].blank is None
                                                      or doc.pages[p-1].blank.verdict != "blank")]
        results = autoname.analyze_directions(doc, renderer, candidates)
    finally:
        renderer.close()
    dt = time.time() - t0
    return dict(case=case, pages=doc.page_count, dt=dt,
                truth=truth, results={r.page: r.suggested_angle for r in results})


def _run_stress81() -> dict:
    ref = json.load(open("stress_v19/stress_result.json", encoding="utf-8"))
    truth = {int(k): int(v) for k, v in ref["rotation_suggestions"].items()}
    pdf = "stress_v19/stress81.pdf"
    reader, warns = open_document(pdf)
    doc = ScanDocument(pdf, reader, warns)
    renderer = PageRenderer(pdf)
    t0 = time.time()
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(), lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        candidates = [p for p in range(1, doc.page_count + 1)
                      if p not in doc.separators
                      and (doc.pages[p-1].blank is None or doc.pages[p-1].blank.verdict != "blank")]
        results = autoname.analyze_directions(doc, renderer, candidates)
    finally:
        renderer.close()
    dt = time.time() - t0
    return dict(case="E(stress81)", pages=doc.page_count, dt=dt,
                truth=truth, results={r.page: r.suggested_angle for r in results})


def _report(row: dict) -> dict:
    truth = row["truth"]
    got = {p: a for p, a in row["results"].items() if a not in (None, 0)}
    hit = sum(1 for p, a in truth.items() if got.get(p) == a)
    miss = [(p, a) for p, a in truth.items() if p not in got]
    wrong = [(p, got[p], a) for p, a in truth.items() if p in got and got[p] != a]
    fp = [(p, a) for p, a in got.items() if p not in truth]
    print(f"[{row['case']}] 页={row['pages']} 耗时={row['dt']:.0f}s")
    print(f"    方向命中 {hit}/{len(truth)} = {hit/max(len(truth),1):.3f}")
    if miss:
        print(f"    ⚠ 漏判: {miss}")
    if wrong:
        print(f"    ❌ 判错: {wrong} (页,得到,真值)")
    if fp:
        print(f"    ❌ 误报: {fp}")
    else:
        print("    误报: 无 ✅")
    return dict(hit=hit, total=len(truth), miss=miss, wrong=wrong, fp=fp)


def main():
    cases = sys.argv[1:] or ["all"]
    if cases == ["all"]:
        cases = list(KIT) + ["E"]
    rows = []
    for c in cases:
        row = _run_stress81() if c == "E" else _run_kit(c)
        rows.append(_report(row))
    th = sum(r["hit"] for r in rows)
    tt = sum(r["total"] for r in rows)
    print("\n=== 汇总 ===")
    print(f"方向准确率 = {th}/{tt} = {th/max(tt,1):.3f}")
    print(f"判错 = {sum(len(r['wrong']) for r in rows)}  误报 = {sum(len(r['fp']) for r in rows)}"
          f"  漏判 = {sum(len(r['miss']) for r in rows)}")


if __name__ == "__main__":
    main()
