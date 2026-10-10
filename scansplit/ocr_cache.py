"""OCR 结果内存 LRU 缓存（v1.9 任务 B）。

只缓存"识别结果行列表"，不缓存渲染图像（渲染已有 PageRenderer 的
LRU 图像缓存）。硬约束：缓存开关打开/关闭时，同样输入与参数产生
完全一致的识别结果——本模块不参与任何识别逻辑，只原样存取。

缓存 key（缺一不可）：
  (源文件路径, mtime_ns, size, 页码, 用户旋转角, OCR 模式, 模型标识, 渲染宽度)
- OCR 模式："cls"（常规识别）与 "nocls"（NoCls 方向分析）严格隔离；
- 模型标识取本地模型目录路径，模型更换后 key 全量变化；
- 渲染宽度即预处理参数（当前预处理仅含缩放）；
- 源文件被修改（mtime/size 变化）→ key 不同，旧条目由 LRU 自然淘汰。

有界性：字节预算（默认 32MB）+ 条目上限（默认 4096），超限逐出最旧。
不可变性：命中返回 OcrLine 的逐项浅拷贝，调用方修改不影响缓存内容。
开关：环境变量 SCANPLIT_OCR_CACHE=0 关闭（默认开），或 set_enabled()。
统计：hits/misses/evictions，用于命中率报告。
"""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from pathlib import Path

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_ITEMS = 4096

_ENV_TOGGLE = "SCANPLIT_OCR_CACHE"

_model_tag_cache: str | None = None


def _model_tag() -> str:
    """本地模型目录标识（安装不变则恒定；模型更换后全量失效）。"""
    global _model_tag_cache
    if _model_tag_cache is None:
        try:
            from .ocr import model_dir

            md = model_dir()
            _model_tag_cache = str(md) if md else "no-model"
        except Exception:
            _model_tag_cache = "model-unknown"
    return _model_tag_cache


def _file_signature(path: str | Path) -> tuple:
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


class OcrCache:
    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES, max_items: int = DEFAULT_MAX_ITEMS):
        self.max_bytes = max_bytes
        self.max_items = max_items
        self._store: "OrderedDict[tuple, list]" = __import__("collections").OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self._enabled = os.environ.get(_ENV_TOGGLE, "1") != "0"

    # -- 开关 ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    # -- 统计 ------------------------------------------------------------------

    def reset_stats(self) -> None:
        self.hits = self.misses = self.evictions = 0

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else None

    def stats(self) -> dict:
        with self._lock:
            return {
                "enabled": self._enabled,
                "entries": len(self._store),
                "bytes": self._bytes,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate": self.hit_rate,
            }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._bytes = 0

    # -- 存取 ------------------------------------------------------------------

    @staticmethod
    def make_key(renderer, idx0: int, width_px: int, user_rotation: int, mode: str) -> tuple:
        """由渲染器与调用参数构造完整缓存 key。

        renderer 须提供 .path（源文件路径）；文件 mtime/size 参与 key，
        源文件变化后旧 key 自然失效。
        """
        return (
            str(Path(renderer.path)), *_file_signature(renderer.path),
            idx0 + 1, int(user_rotation), mode, _model_tag(), int(width_px),
        )

    def _copy(self, lines: list) -> list:
        return [replace(l) for l in lines]

    def get(self, key: tuple) -> list | None:
        with self._lock:
            val = self._store.get(key)
            if val is None:
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return self._copy(val[1])

    def put(self, key: tuple, lines: list) -> None:
        if not lines:
            return  # 空结果（识别失败/空白页）不缓存，避免负缓存掩盖临时故障
        size = sum(len(l.text.encode("utf-8", "ignore")) + 96 for l in lines)
        with self._lock:
            if key in self._store:
                old = self._store.pop(key)
                self._bytes -= old[0]
            self._store[key] = (size, [replace(l) for l in lines])
            self._bytes += size
            while self._store and (
                len(self._store) > self.max_items or self._bytes > self.max_bytes
            ):
                _, (sz, _) = self._store.popitem(last=False)
                self._bytes -= sz
                self.evictions += 1


_cache: OcrCache | None = None
_cache_lock = threading.Lock()


def get_cache() -> OcrCache:
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = OcrCache()
        return _cache


def recognize_cached(engine, mode: str, renderer, idx0: int, width_px: int,
                     user_rotation: int = 0) -> list:
    """带缓存的 OCR 识别入口（结果与直接 engine.recognize(render(img)) 完全一致）。

    由 autoname 的常规识别（mode="cls"）与方向分析（mode="nocls"）调用；
    两个模式的 key 互相隔离，绝不互串。
    """
    cache = get_cache()
    img = renderer.render(idx0, width_px=width_px, user_rotation=user_rotation)
    if not cache.enabled:
        return engine.recognize(img)
    key = OcrCache.make_key(renderer, idx0, width_px, user_rotation, mode)
    hit = cache.get(key)
    if hit is not None:
        return hit
    lines = engine.recognize(img)
    cache.put(key, lines)
    return lines
