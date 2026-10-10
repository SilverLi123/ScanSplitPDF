# -*- coding: utf-8 -*-
"""核查 on2 的 37 次 cache miss 构成：是否全部为"空结果不入缓存"的调用。

方法：冷跑 naming+direction（缓存开启），挂钩 cache.get 记录 miss key；
跑完后核对每个 miss key 是否不在 cache._store 中（put 只存非空结果）；
对抽样 miss key 关闭缓存直接调 recognize_cached，证实识别结果为空。
输出 stress_v19/miss_check.json。
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scansplit import autoname, blankdetect, grouping, loader
from scansplit.model import ScanDocument
from scansplit.ocr import OcrEngine, OcrEngineNoCls
from scansplit.ocr_cache import get_cache
from scansplit.render import PageRenderer

PDF = Path("stress_v19/stress81.pdf")


def main():
    cache = get_cache()
    cache.clear()
    cache.set_enabled(True)
    cache.reset_stats()

    miss_keys = []
    orig_get = cache.get

    def get(key):
        v = orig_get(key)
        if v is None:
            miss_keys.append(key)
        return v

    cache.get = get

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

    verdicts = blankdetect.detect_document(
        renderer, n, blankdetect.DetectParams(), has_text)
    for page, r in zip(doc.pages, verdicts):
        page.blank = r
    doc.status = "analyzed"
    grouping.auto_group(doc)

    t0 = time.perf_counter()
    autoname.auto_name_groups(doc, renderer, engine=engine)
    t_naming = round(time.perf_counter() - t0, 1)

    cands = [p for p in range(1, n + 1) if p not in doc.separators]
    t0 = time.perf_counter()
    autoname.analyze_directions(doc, renderer, cands, progress_cb=lambda i, nn: None)
    t_dir = round(time.perf_counter() - t0, 1)
    renderer.close()

    cache.get = orig_get
    stats = cache.stats()
    stored = set(cache._store.keys())

    misses_unique = sorted(set(miss_keys))
    never_stored = [k for k in misses_unique if k not in stored]
    # 抽样验证：关闭缓存直调，确认结果为空
    sample = never_stored[:5] + never_stored[-3:] if never_stored else []
    cache.set_enabled(False)
    sample_empty = []
    if sample:
        renderer2 = PageRenderer(PDF)
        eng_nocls = OcrEngineNoCls.get()
        for key in sample:
            # key = (path, mtime_ns, size, page, rotation, mode, model_tag, width)
            _path, _mt, _sz, page1, rot, mode, _tag, width = key
            img = renderer2.render(page1 - 1, width, bucket="verify")
            if img is None:
                sample_empty.append({"key": str(key), "render": "FAIL"})
                continue
            eng = eng_nocls if mode == "nocls" else engine
            lines = eng.recognize(img)
            sample_empty.append({
                "page": page1, "rot": rot, "mode": mode, "width": width,
                "lines": len(lines),
            })
        renderer2.close()

    out = {
        "total_get_calls": len(miss_keys),
        "miss_get_calls": len(miss_keys),
        "unique_miss_keys": len(misses_unique),
        "all_misses_never_stored": len(never_stored) == len(misses_unique),
        "stats": stats,
        "naming_s": t_naming, "direction_s": t_dir,
        "sample_check": sample_empty,
    }
    Path("stress_v19/miss_check.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
