# -*- coding: utf-8 -*-
"""探查：内部空白页 vs 分隔页的"下一页首页特征"可判别性。"""
import sys, json, csv
sys.path.insert(0, '.')
from scansplit.render import PageRenderer
from scansplit.ocr import OcrEngine
from scansplit import ocrfields, pagenum

CASES = [('B', 'B_双面灰底与噪点_104页_纯图片.pdf'),
         ('C', 'C_连续多页侧放表格_108页_纯图片.pdf'),
         ('D', 'D_复杂边界与命名_100页_纯图片.pdf')]

for case, pdf in CASES:
    ans = json.load(open(f'stress_v19/kit100/{case}_标准答案.json', encoding='utf-8'))
    rows = {int(r['PDF页码']): r for r in csv.DictReader(
        open(f'stress_v19/kit100/{case}_逐页答案.csv', encoding='utf-8-sig'))}
    r = PageRenderer(f'stress_v19/kit100/{pdf}')
    eng = OcrEngine.get()
    n = ans['pages']

    def next_content(p):
        q = p + 1
        while q <= n and rows[q]['页面类别'] != 'content':
            q += 1
        return q if q <= n else None

    print(f'=== {case} ===', flush=True)
    for kind, pages in (('SEP', ans['separator_blank_pages_remove']),
                        ('INT', ans['internal_blank_pages_keep'])):
        for p in pages:
            nc = next_content(p)
            if nc is None:
                continue
            img = r.render(nc - 1, width_px=1000, bucket='probe')
            ls = eng.recognize(img)
            w, h = img.size
            tl = ocrfields.title_likeness(ls, w, h)
            pn = pagenum.extract_page_number(ls, h) if ls else None
            print(f'  {kind} p{p:>3} -> next content p{nc:>3} '
                  f'(in_doc={rows[nc]["文档内页码"]}) tl={tl:.2f} pageno={pn} nlines={len(ls)}',
                  flush=True)
    r.close()
