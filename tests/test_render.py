"""渲染器缓存：按内存字节预算淘汰，防止高倍预览大图撑爆内存。"""

import scansplit.render as render_mod
from scansplit.render import PageRenderer


def _bytes_of(img) -> int:
    return img.width * img.height * (3 if img.mode == "RGB" else 1)


def test_cache_byte_budget(tmp_path, make_scan, monkeypatch):
    pdf = make_scan("cache.pdf", ["text", "text", "text"])
    monkeypatch.setattr(render_mod, "MAX_CACHE_BYTES", 5_000_000)
    r = PageRenderer(pdf, cache_size=100)
    try:
        img0 = r.render(0, width_px=1000)
        assert r._cache_bytes == _bytes_of(img0)
        r.render(1, width_px=1000)
        r.render(2, width_px=1000)
        # 单幅 1000px 渲染约 4.2MB，预算 5MB：只留最近一幅
        assert len(r._cache) == 1
        assert r._cache_bytes <= render_mod.MAX_CACHE_BYTES
        assert (2, "thumb", 1000, 0) in r._cache
    finally:
        r.close()
        assert r._cache_bytes == 0 and len(r._cache) == 0


def test_cache_lru_entry_cap(tmp_path, make_scan):
    pdf = make_scan("lru.pdf", ["text"] * 6)
    r = PageRenderer(pdf, cache_size=3)
    try:
        for i in range(5):
            r.render(i, width_px=150)
        assert len(r._cache) == 3
        assert (0, "thumb", 150, 0) not in r._cache
    finally:
        r.close()
