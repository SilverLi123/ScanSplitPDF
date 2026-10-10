# -*- coding: utf-8 -*-
"""OCR 效率实测：真实 11 页样本，逐页渲染+识别计时。"""
import sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:/vibe-coding/scansplit-pdf")

from scansplit.render import PageRenderer
from scansplit.ocr import OcrEngine, OcrEngineNoCls

SRC = r"C:/Users/li333/Downloads/%E7%BA%AF%E5%9B%BE%E7%89%87PDF_%E5%A4%9A%E6%96%87%E6%A1%A3%E7%A9%BA%E7%99%BD%E9%A1%B5%E5%88%86%E9%9A%94_%E6%B5%8B%E8%AF%95%E6%A0%B7%E6%9C%AC.pdf"

t0 = time.perf_counter()
renderer = PageRenderer(SRC)
n = len(renderer._pdf)
print(f"渲染器初始化: {time.perf_counter()-t0:.2f}s  页数={n}")

t0 = time.perf_counter()
eng = OcrEngine.get()
ok = eng.available
print(f"OCR 引擎加载(cls): {time.perf_counter()-t0:.2f}s  可用={ok}")

t0 = time.perf_counter()
raw = OcrEngineNoCls.get()
ok2 = raw.available
print(f"OCR 引擎加载(no-cls): {time.perf_counter()-t0:.2f}s  可用={ok2}")

total = 0.0
for i in range(n):
    t0 = time.perf_counter()
    img = renderer.render(i, width_px=1200, bucket="ocr")
    t_render = time.perf_counter() - t0
    t0 = time.perf_counter()
    lines = eng.recognize(img)
    t_ocr = time.perf_counter() - t0
    total += t_render + t_ocr
    n_text = sum(len(l.text) for l in lines)
    print(f"页{i+1:2d}: 渲染 {t_render:.2f}s + 识别 {t_ocr:.2f}s = {t_render+t_ocr:.2f}s  行数={len(lines)} 字数={n_text}")
print(f"{n} 页合计: {total:.2f}s  平均 {total/n:.2f}s/页")
