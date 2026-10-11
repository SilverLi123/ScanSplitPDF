"""按需渲染 PDF 页面为图片（仅用于界面预览与空白检测，不参与导出）。"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import threading
from functools import wraps

import pypdfium2 as pdfium
from PIL import Image

THUMB_WIDTH = 150
DETECT_WIDTH = 1000
MAX_CACHE_BYTES = 512 * 1024 * 1024  # 缓存内存上限（预览 4x 缩放单页可达 ~33MB）
_PDFIUM_LOCK = threading.RLock()  # PDFium is not thread safe, even across documents.


def _pdfium_locked(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _PDFIUM_LOCK:
            return fn(*args, **kwargs)
    return call


class RenderError(Exception):
    pass


class PageRenderer:
    """单文档渲染器。必须在同一线程使用（Tk 主线程）。LRU 缓存渲染结果。"""

    @_pdfium_locked
    def __init__(self, path: str | Path, cache_size: int = 400):
        self.path = Path(path)
        try:
            self._pdf = pdfium.PdfDocument(str(self.path))
        except Exception as e:
            raise RenderError(f"无法渲染 PDF：{self.path.name}\n{e}") from e
        self._cache: OrderedDict[tuple, Image.Image] = OrderedDict()
        self._cache_size = cache_size
        self._cache_bytes = 0

    def clear_cache(self):
        self._cache.clear()
        self._cache_bytes = 0

    @_pdfium_locked
    def close(self) -> None:
        self.clear_cache()
        try:
            self._pdf.close()
        except Exception:
            pass

    # -- 页面几何 ----------------------------------------------------------

    @_pdfium_locked
    def page_size_pt(self, idx0: int) -> tuple[float, float]:
        """包含源 /Rotate 的页面尺寸（点）。"""
        page = self._pdf[idx0]
        try:
            w, h = page.get_size()
        finally:
            page.close()
        return float(w), float(h)

    @_pdfium_locked
    def stored_rotation(self, idx0: int) -> int:
        try:
            page = self._pdf[idx0]
            try:
                return int(page.get_rotation() or 0) % 360
            finally:
                page.close()
        except Exception:
            return 0

    def display_size_pt(self, idx0: int, user_rotation: int = 0) -> tuple[float, float]:
        w, h = self.page_size_pt(idx0)
        # PDFium get_size already includes the page's stored rotation.
        if user_rotation % 180 == 90:
            return h, w
        return w, h

    # -- 渲染 ----------------------------------------------------------------

    @_pdfium_locked
    def render(
        self,
        idx0: int,
        width_px: int = THUMB_WIDTH,
        scale: float | None = None,
        user_rotation: int = 0,
        bucket: str = "thumb",
    ) -> Image.Image:
        total_rot = (self.stored_rotation(idx0) + (user_rotation % 360)) % 360
        key = (idx0, bucket, int(width_px) if scale is None else int(scale * 1000), total_rot)
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
            return img
        if scale is None:
            w_pt, _ = self.display_size_pt(idx0, user_rotation)
            scale = max(width_px, 1) / max(w_pt, 1.0)
        page, bitmap = None, None
        try:
            page = self._pdf[idx0]
            # PDFium render(rotation=...) is ADDITIONAL rotation, not absolute.
            bitmap = page.render(scale=scale, rotation=user_rotation % 360)
            img = bitmap.to_pil().copy()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
        except Exception as e:
            raise RenderError(f"渲染第 {idx0 + 1} 页失败：{e}") from e
        finally:
            if bitmap is not None:
                bitmap.close()
            if page is not None:
                page.close()
        self._cache[key] = img
        self._cache_bytes += img.width * img.height * (3 if img.mode == "RGB" else 1)
        while len(self._cache) > 1 and (
            len(self._cache) > self._cache_size or self._cache_bytes > MAX_CACHE_BYTES
        ):
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.width * old.height * (3 if old.mode == "RGB" else 1)
        return img

    def render_thumb(self, idx0: int, user_rotation: int = 0) -> Image.Image:
        img = self.render(idx0, width_px=THUMB_WIDTH, user_rotation=user_rotation, bucket="thumb")
        return img

    def render_detect(self, idx0: int, user_rotation: int = 0) -> Image.Image:
        return self.render(idx0, width_px=DETECT_WIDTH, user_rotation=user_rotation, bucket="detect")
