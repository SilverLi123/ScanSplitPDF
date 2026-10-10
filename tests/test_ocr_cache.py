# -*- coding: utf-8 -*-
"""OCR LRU 缓存回归（v1.9 任务 B）。

硬约束：缓存开关打开/关闭、命中/未命中必须产生完全一致的识别结果；
key 覆盖文档标识/页码/旋转/OCR 模式/模型/渲染宽度；缓存内容不可被
调用方修改；有界内存；源文件变化失效；跨文档隔离。
"""

import os

import pytest

from scansplit import ocr_cache
from scansplit.ocr import OcrLine


class FakeLine:
    """用轻量替身测 LRU 行为（replace() 需 dataclass，见 copy 专测用真 OcrLine）。"""

    def __init__(self, text, score, box):
        self.text, self.score, self.box = text, score, box


class FakeRenderer:
    def __init__(self, path):
        self.path = path


@pytest.fixture
def cache(monkeypatch):
    c = ocr_cache.OcrCache(max_bytes=10 * 1024 * 1024, max_items=64)
    monkeypatch.setattr(ocr_cache, "get_cache", lambda: c)
    return c


def _key(renderer, page=1, rot=0, mode="cls", width=1000):
    return ocr_cache.OcrCache.make_key(renderer, page - 1, width, rot, mode)


def test_hit_miss_identical_results(cache, tmp_path):
    """命中与未命中必须返回完全一致的结果（硬约束）。"""
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    r = FakeRenderer(f)
    key = _key(r)
    lines = [OcrLine("标题文字", 0.9, [[0, 0], [10, 0], [10, 5], [0, 5]])]
    cache.put(key, lines)
    got = cache.get(key)
    assert [(l.text, l.score, l.box) for l in got] == [
        (l.text, l.score, l.box) for l in lines]
    assert cache.hits == 1 and cache.misses == 0
    assert cache.get(_key(r, page=2)) is None  # 不同页 → miss
    assert cache.misses == 1


def test_mode_rotation_width_doc_isolation(cache, tmp_path):
    """cls/nocls 严格隔离；旋转/宽度/文档任一变化都是不同 key。"""
    fa, fb = tmp_path / "a.pdf", tmp_path / "b.pdf"
    fa.write_bytes(b"a")
    fb.write_bytes(b"b")
    ra, rb = FakeRenderer(fa), FakeRenderer(fb)
    base = _key(ra)
    cache.put(base, [OcrLine("x", 0.9, [])])
    assert cache.get(base) is not None
    # 同文档其他维度全部 miss
    assert cache.get(_key(ra, mode="nocls")) is None      # 模式隔离
    assert cache.get(_key(ra, rot=90)) is None            # 旋转
    assert cache.get(_key(ra, width=1200)) is None        # 渲染宽度
    assert cache.get(_key(rb)) is None                    # 跨文档隔离
    assert cache.get(_key(ra, page=3)) is None            # 页码


def test_cached_result_mutation_does_not_leak(cache, tmp_path):
    """调用方修改返回的行，不得影响缓存内容。"""
    f = tmp_path / "m.pdf"
    f.write_bytes(b"x")
    r = FakeRenderer(f)
    key = _key(r)
    cache.put(key, [OcrLine("原文", 0.9, [[1, 1]])])
    got = cache.get(key)
    got[0].text = "被篡改"
    got[0].score = 0.1
    got.append(OcrLine("多出来的", 1.0, []))
    again = cache.get(key)
    assert again[0].text == "原文" and again[0].score == 0.9
    assert len(again) == 1


def test_lru_memory_bound_evicts_oldest(cache, tmp_path):
    """超出条目上限逐出最旧，缓存有界。"""
    cache.max_items = 3
    f = tmp_path / "l.pdf"
    f.write_bytes(b"x")
    r = FakeRenderer(f)
    keys = [_key(r, page=i) for i in range(1, 6)]
    for k in keys:
        cache.put(k, [OcrLine("t" * 10, 0.9, [])])
    assert len(cache._store) == 3
    assert cache.get(keys[0]) is None  # 最旧被逐出
    assert cache.get(keys[4]) is not None
    assert cache.evictions == 2


def test_source_change_invalidates(cache, tmp_path):
    """源文件 mtime/size 变化 → key 变化，新签名不会读到旧内容。"""
    f = tmp_path / "s.pdf"
    f.write_bytes(b"x")
    r = FakeRenderer(f)
    key = _key(r)
    cache.put(key, [OcrLine("旧内容", 0.9, [])])
    st = os.stat(f)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert cache.get(_key(r)) is None  # 新签名 miss（不会读到旧内容）
    cache.put(_key(r), [OcrLine("新内容", 0.9, [])])
    got = cache.get(_key(r))
    assert got[0].text == "新内容"     # 新签名使用新结果


def test_disable_switch_bypasses_cache(cache, tmp_path):
    """开关关闭：recognize_cached 每次都走引擎（等价未缓存），结果不变。"""
    from scansplit.ocr import OcrLine

    cache.set_enabled(False)
    assert cache.enabled is False
    f = tmp_path / "d.pdf"
    f.write_bytes(b"x")

    calls = {"n": 0}

    class FakeEngine:
        def recognize(self, img):
            calls["n"] += 1
            return [OcrLine("固定文本", 0.9, [[0, 0]])]

    class FakeR:
        path = f

        def render(self, idx0, width_px=100, user_rotation=0, **k):
            return "IMG"

    fr = FakeR()
    a = ocr_cache.recognize_cached(FakeEngine(), "cls", fr, 0, 1000)
    b = ocr_cache.recognize_cached(FakeEngine(), "cls", fr, 0, 1000)
    assert calls["n"] == 2  # 关闭时引擎被调用两次（无缓存）
    assert [(l.text, l.score) for l in a] == [(l.text, l.score) for l in b]
    assert cache.stats()["entries"] == 0  # 关闭时不写缓存


def test_recognize_cached_parity_on_off(cache, tmp_path, monkeypatch):
    """recognize_cached 开/关两种状态下结果逐行一致（不改变识别行为）。"""
    from scansplit.ocr import OcrLine

    f = tmp_path / "p.pdf"
    f.write_bytes(b"x")
    r = FakeRenderer(f)

    class FakeEngine:
        def recognize(self, img):
            return [OcrLine("固定文本", 0.88, [[0, 0], [9, 0]])]

    class FakeR:
        path = f

        def render(self, idx0, width_px=100, user_rotation=0, **k):
            return "IMG"

    fr = FakeR()
    cache.set_enabled(False)
    off = ocr_cache.recognize_cached(FakeEngine(), "cls", fr, 0, 1000)
    cache.set_enabled(True)
    on1 = ocr_cache.recognize_cached(FakeEngine(), "cls", fr, 0, 1000)
    on2 = ocr_cache.recognize_cached(FakeEngine(), "cls", fr, 0, 1000)  # 命中
    assert [(l.text, l.score, l.box) for l in off] == \
        [(l.text, l.score, l.box) for l in on1] == \
        [(l.text, l.score, l.box) for l in on2]
    assert cache.hits == 1 and cache.misses == 1  # on1 未命中 on2 命中


def test_empty_result_not_cached(cache, tmp_path):
    """空结果（识别失败/空白页）不缓存：避免负缓存掩盖临时故障。"""
    f = tmp_path / "e.pdf"
    f.write_bytes(b"x")
    r = FakeRenderer(f)
    cache.put(_key(r), [])
    assert len(cache._store) == 0
