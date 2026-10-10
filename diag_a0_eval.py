# -*- coding: utf-8 -*-
"""阶段 A0 验收：对四套 100 页样本，用**真实**的 splitctx.judge_blank_context
判定每个空白候选页的业务属性，与标准答案比对，输出：
  - 分隔页 P/R（应判 separator 的页）
  - 内部空白保留率（必须判为保留 = internal_blank 或 uncertain）
  - 误排除（内部空白被误判 separator 而丢页）——必须为 0
  - 漏分隔（真分隔被误判保留）——可由后续复核兜底，但需统计

用法： ./.venv/Scripts/python.exe diag_a0_eval.py [A|B|C|D|all]
"""
import csv
import json
import sys
import time

sys.path.insert(0, ".")

from scansplit import blankdetect, splitctx
from scansplit.loader import open_document
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine
from scansplit.render import PageRenderer

PDFS = {
    "A": "A_标准空白分隔_100页_纯图片.pdf",
    "B": "B_双面灰底与噪点_104页_纯图片.pdf",
    "C": "C_连续多页侧放表格_108页_纯图片.pdf",
    "D": "D_复杂边界与命名_100页_纯图片.pdf",
}


def run(case: str) -> dict:
    base = f"stress_v19/kit100/{case}"
    ans = json.load(open(f"{base}_标准答案.json", encoding="utf-8"))
    truth = {int(r["PDF页码"]): r for r in csv.DictReader(
        open(f"{base}_逐页答案.csv", encoding="utf-8-sig"))}
    pdf = f"stress_v19/kit100/{PDFS[case]}"

    reader, warns = open_document(pdf)
    doc = ScanDocument(pdf, reader, warns)
    renderer = PageRenderer(pdf)
    engine = OcrEngine.get()
    t0 = time.time()
    try:
        verdicts = blankdetect.detect_document(
            renderer, doc.page_count, blankdetect.DetectParams(), lambda i: False)
        for page, r in zip(doc.pages, verdicts):
            page.blank = r
        evs = splitctx.judge_blank_context(doc, renderer, engine)
        counts = splitctx.apply_context(doc, evs)
    finally:
        renderer.close()
    dt = time.time() - t0

    sep_truth = set(ans["separator_blank_pages_remove"])
    int_truth = set(ans["internal_blank_pages_keep"])
    got_sep = set(counts[splitctx.SEPARATOR])
    got_int = set(counts[splitctx.INTERNAL_BLANK])
    got_unc = set(counts[splitctx.UNCERTAIN])

    sep_hit = sep_truth & got_sep
    int_lost = int_truth & got_sep                 # 内部空白被误排除 → 严重
    int_kept = int_truth & (got_int | got_unc)     # 内部空白被保留
    sep_missed = sep_truth & (got_int | got_unc)   # 真分隔未自动识别（降到保留）

    # 任务书点名"必须保留"的重点页（= 各样本 internal_blank_pages_keep）
    must_keep = int_truth
    must_kept = int_truth & (got_int | got_unc)
    must_lost = int_truth & got_sep

    return dict(
        case=case, pages=ans["pages"], dt=dt,
        sep_truth=len(sep_truth), sep_hit=len(sep_hit), sep_missed=len(sep_missed),
        int_truth=len(int_truth), int_kept=len(int_kept), int_lost=len(int_lost),
        n_blank_cand=len(evs), n_sep=len(got_sep), n_int=len(got_int), n_unc=len(got_unc),
        lost_pages=sorted(int_lost), missed_sep=sorted(sep_missed),
        must_keep=sorted(must_keep), must_kept=sorted(must_kept),
        must_lost=sorted(must_lost),
    )


def main():
    cases = sys.argv[1:] or ["all"]
    if cases == ["all"]:
        cases = list(PDFS)
    rows = []
    for c in cases:
        r = run(c)
        rows.append(r)
        print(f"[{c}] 页={r['pages']} 空白候选={r['n_blank_cand']} 耗时={r['dt']:.0f}s")
        print(f"    分隔: 判定 {r['n_sep']}（真值 {r['sep_truth']}，命中 {r['sep_hit']}，"
              f"漏判 {r['sep_missed']}）")
        print(f"    内部: 真值 {r['int_truth']}，保留 {r['int_kept']}，"
              f"误排除 {r['int_lost']}  {'✅' if r['int_lost']==0 else '❌ 丢页!'}")
        print(f"    uncertain={r['n_unc']} internal={r['n_int']}")
        print(f"    重点保留页 (必须保留): {r['must_keep']} → 保留 {r['must_kept']}"
              f"  {'✅' if not r['must_lost'] else '❌ 丢失' + str(r['must_lost'])}")
        if r["lost_pages"]:
            print(f"    ❌ 误排除页: {r['lost_pages']}")
        if r["missed_sep"]:
            print(f"    ⚠ 漏自动拆分的分隔页: {r['missed_sep']}")

    tot_sep = sum(r["sep_truth"] for r in rows)
    tot_hit = sum(r["sep_hit"] for r in rows)
    tot_int = sum(r["int_truth"] for r in rows)
    tot_kept = sum(r["int_kept"] for r in rows)
    tot_lost = sum(r["int_lost"] for r in rows)
    print("\n=== 汇总 ===")
    print(f"分隔自动判定 recall = {tot_hit}/{tot_sep} = {tot_hit/max(tot_sep,1):.3f}"
          f"  precision = {tot_hit/max(sum(r['n_sep'] for r in rows),1):.3f}")
    print(f"内部空白保留率 = {tot_kept}/{tot_int} = {tot_kept/max(tot_int,1):.3f}")
    print(f"内部空白误排除（丢页）= {tot_lost}  {'✅ 0 丢页' if tot_lost==0 else '❌'}")
    print(f"总耗时 = {sum(r['dt'] for r in rows):.0f}s")


if __name__ == "__main__":
    main()
