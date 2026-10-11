# -*- coding: utf-8 -*-
"""阶段 A1 诊断：对 stress81 样本跑真实 analyze_directions，
与 stress_result.json 的方向真值比对，报告当前方向识别实际状态。

用法： ./.venv/Scripts/python.exe diag_a1_dir.py
"""
import json
import sys
import time

sys.path.insert(0, ".")

from scansplit import autoname, blankdetect
from scansplit.loader import open_document
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from scansplit.render import PageRenderer


def main():
    pdf = "stress_v19/stress81.pdf"
    ref = json.load(open("stress_v19/stress_result.json", encoding="utf-8"))
    truth = {int(k): v for k, v in ref["rotation_suggestions"].items()}

    reader, warns = open_document(pdf)
    doc = ScanDocument(pdf, reader, warns)
    renderer = PageRenderer(pdf)
    engine = OcrEngine.get()
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(), lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        candidates = [p for p in range(1, doc.page_count + 1) if p not in doc.separators]
        t0 = time.time()
        results = autoname.analyze_directions(doc, renderer, candidates)
        dt = time.time() - t0
    finally:
        renderer.close()

    got = {r.page: r.suggested_angle for r in results if r.suggested_angle not in (None, 0)}
    hit = 0
    miss = []
    wrong = []
    for p, ang in truth.items():
        if got.get(p) == ang:
            hit += 1
        elif p in got:
            wrong.append((p, got[p], ang))
        else:
            miss.append((p, ang))
    # 误报：真值不需要旋转但被建议旋转
    fp = [(p, a) for p, a in got.items() if p not in truth and a not in (None, 0)]

    print(f"样本 stress81：{doc.page_count} 页，候选 {len(candidates)} 页，耗时 {dt:.0f}s")
    print(f"方向命中 {hit}/{len(truth)} = {hit/len(truth):.3f}")
    if miss:
        print(f"  漏判（未给角度）: {miss}")
    if wrong:
        print(f"  判错角度: {wrong}  (页, 得到, 真值)")
    if fp:
        print(f"  ❌ 误报: {fp}")
    else:
        print("  误报: 无 ✅")


if __name__ == "__main__":
    main()
