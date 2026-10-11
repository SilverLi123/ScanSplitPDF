# -*- coding: utf-8 -*-
"""探查：为 splitctx 设计"多证据打分"判别器所需的特征分布。

特征（全部来自下一内容页顶部结构，语义化、非样本专用）：
  T  顶部居中大字号标题行（y0<0.30h, h>=max(1.25*medh, 0.02h), |cx-w/2|<0.22w, 非序号行）
  W  文头（发文机关，如"XX处文件/XX办公室"，居中、位于标题之上、字号 <= 标题）
  H  主送机关/正文起始标记（"各XX：" 行紧跟标题之后）
  C  续表/续页标记（"续表""（续）""续页"）
  N  紧跟顶部标题的"序号小节标题"（一、二、…）——续页特征
  L  标题是否页面上最大字号行
输出每个样本的特征，用于挑阈值。
"""
import sys, json, csv, re
sys.path.insert(0, '.')
from scansplit.render import PageRenderer
from scansplit.ocr import OcrEngine
from scansplit import ocrfields

SECTION_RE = re.compile(r"^[一二三四五六七八九十\d]+[、.．）)]")
CONT_RE = re.compile(r"[（(]?\s*[续继续页表]\s*[表页]?\s*[)）]?\s*$|[（(]\s*续\s*[）)]|续表|续页")
HEADER_RE = re.compile(r".{2,12}(?:办公室|委员会|领导小组|管理处|管理局|处|部|厅|局|科|中心|学院|大学|医院)\s*(?:文件|函)?$")
ADDR_RE = re.compile(r"^各.{0,12}(?:单位|部门|学院|处室|科室|同志|有关)[：:]?$")


def feats(lines, w, h):
    if not lines:
        return None
    Hs = [l.height for l in lines]
    medh = sorted(Hs)[len(Hs) // 2]
    maxh = max(Hs)
    top = sorted([l for l in lines if l.y0 < h * 0.32], key=lambda l: l.y0)
    cand = [l for l in top
            if l.height >= max(medh * 1.25, h * 0.02)
            and abs(l.cx - w / 2) < w * 0.22
            and not ocrfields._is_date_or_pageno(l)
            and not SECTION_RE.match(l.text.strip())]
    title = max(cand, key=lambda l: l.height) if cand else None
    T = title is not None
    W = any(HEADER_RE.search(l.text.strip()) and l.height <= (title.height if title else 1e9)
            for l in top if l is not title)
    H = any(ADDR_RE.match(l.text.strip()) for l in top)
    C = any(CONT_RE.search(l.text.strip()) for l in top)
    N = any(SECTION_RE.match(l.text.strip()) for l in top)
    L = bool(title) and title.height >= maxh - 1
    return dict(T=int(T), W=int(W), H=int(H), C=int(C), N=int(N), L=int(L),
                medh=medh, maxh=maxh, mx=round(maxh / max(medh, 1), 2))


CASES = {'A': 'A_标准空白分隔_100页_纯图片.pdf', 'B': 'B_双面灰底与噪点_104页_纯图片.pdf',
         'C': 'C_连续多页侧放表格_108页_纯图片.pdf', 'D': 'D_复杂边界与命名_100页_纯图片.pdf'}
rows_out = []
for case, pdf in CASES.items():
    ans = json.load(open(f'stress_v19/kit100/{case}_标准答案.json', encoding='utf-8'))
    rows = {int(r['PDF页码']): r for r in csv.DictReader(
        open(f'stress_v19/kit100/{case}_逐页答案.csv', encoding='utf-8-sig'))}
    r = PageRenderer(f'stress_v19/kit100/{pdf}')
    eng = OcrEngine.get()
    n = ans['pages']

    def nxt(p):
        q = p + 1
        while q <= n and rows[q]['页面类别'] != 'content':
            q += 1
        return q if q <= n else None

    for kind in ('SEP', 'INT'):
        pages = (ans['separator_blank_pages_remove'] if kind == 'SEP'
                 else ans['internal_blank_pages_keep'])
        for p in pages:
            nc = nxt(p)
            if nc is None:
                continue
            img = r.render(nc - 1, width_px=1000, bucket='feat')
            ls = eng.recognize(img)
            f = feats(ls, *img.size)
            if f is None:
                continue
            print(f"{case} {kind} p{p:>3}->{nc:>3} in_doc={rows[nc]['文档内页码']:>3} "
                  f"T={f['T']} W={f['W']} H={f['H']} C={f['C']} N={f['N']} L={f['L']} "
                  f"mx={f['mx']}", flush=True)
    r.close()
