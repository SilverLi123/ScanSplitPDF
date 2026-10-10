"""空白页检测：多信号 + 置信度 + 三级判定（空白 / 疑似 / 有内容）。

算法思路借鉴 scanprep（MIT，https://github.com/baltpeter/scanprep）：
自适应阈值（均值-50）、边缘裁剪（去装订孔/扫描边缘黑线）、形态学闭运算去噪、墨迹比判定。
本项目扩展为多信号模型：

- ink_ratio    自适应阈值下的墨迹像素比（去噪后）
- dark_ratio   绝对深色像素比（去噪后）—— 印章/签名/文字大多是深色
- grad_ratio   梯度像素比 —— 区分"平滑阴影"与"有边缘的浅色内容"
- bbox_frac    内容包围盒面积比（去噪后）
- edge_ink     边缘条带墨迹（仅展示，不参与判定——边缘黑线不应导致误判）
- has_text     文本层信号（有实质文本的页面直接判内容）

浅色印章/签名等模糊页面一律落入"疑似"供人工确认，绝不依赖单一全局阈值自动删除。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .model import BLANK, CONTENT, MAYBE, BlankResult

DARK_LEVEL = 190  # 绝对深色阈值（深墨迹/文字/红章）
GRAD_LEVEL = 12  # 局部梯度阈值（像素级差分）


@dataclass
class DetectParams:
    ink_threshold_pct: float = 0.4  # 判空白的墨迹比上限（%）
    maybe_ink_max_pct: float = 1.5  # 疑似区间的墨迹比上限（%）
    dark_pct_blank: float = 0.02  # 判空白的深色比上限（%）
    dark_pct_content: float = 0.08  # 判内容的深色比下限（%）
    bbox_frac_content: float = 0.05  # 判内容的内容包围盒面积比下限
    grad_blank_max_pct: float = 0.35  # 判空白的梯度比上限（%）
    grad_content_min_pct: float = 0.8  # 判内容的梯度比下限（%）
    edge_ink_max: float = 0.20  # 边缘条带墨迹比（仅作展示信号）
    margin_lr: float = 0.08  # 左右裁剪比例
    margin_tb: float = 0.05  # 上下裁剪比例
    adaptive_offset: float = 50.0  # 自适应阈值偏移：均值 - offset

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "DetectParams":
        p = DetectParams()
        for k in p.to_dict():
            if k in d:
                setattr(p, k, float(d[k]))
        return p


def _crop_margins(gray: np.ndarray, params: DetectParams) -> np.ndarray:
    h, w = gray.shape
    lr = int(w * params.margin_lr)
    tb = int(h * params.margin_tb)
    if lr * 2 >= w or tb * 2 >= h:
        return gray
    return gray[tb : h - tb, lr : w - lr]


def _window_op(binary: np.ndarray, radius: int, op: str) -> np.ndarray:
    """滑动窗口极值滤波（纯 numpy，等价于二值膨胀/腐蚀）。"""
    if not binary.any():
        return binary
    out = binary.copy()
    h, w = binary.shape
    padded = np.pad(binary, radius, mode="edge")
    for dy in range(-radius, radius + 1):
        y0, y1 = radius + dy, radius + dy + h
        for dx in range(-radius, radius + 1):
            x0, x1 = radius + dx, radius + dx + w
            win = padded[y0:y1, x0:x1]
            out = np.maximum(out, win) if op == "max" else np.minimum(out, win)
    return out


def _morph_clean(binary: np.ndarray) -> np.ndarray:
    """先膨胀后腐蚀（闭运算），消除孤立噪点、强化真实内容块。"""
    if not binary.any():
        return binary
    dilated = _window_op(binary, 1, "max")  # 3x3 膨胀
    return _window_op(dilated, 3, "min")  # 7x7 腐蚀


def _grad_ratio(gray_s: np.ndarray) -> float:
    """梯度像素比（%）：局部变化明显的像素占比。阴影平滑 → 低；文字/印章边缘 → 高。"""
    g = gray_s.astype(np.int16)
    dx = np.abs(np.diff(g, axis=1))
    dy = np.abs(np.diff(g, axis=0))
    n = max(dx.size + dy.size, 1)
    return float(((dx >= GRAD_LEVEL).sum() + (dy >= GRAD_LEVEL).sum()) / n * 100.0)


def analyze_gray(gray: np.ndarray, params: DetectParams) -> dict:
    """从灰度图提取空白检测信号。gray: uint8 2D，0=黑 255=白。"""
    crop = _crop_margins(gray, params)
    mean = float(crop.mean()) if crop.size else 255.0
    thresh = max(mean - params.adaptive_offset, 40.0)

    ink = crop < thresh
    dark = crop < DARK_LEVEL
    ink_raw = float(ink.mean() * 100.0) if ink.size else 0.0
    dark_raw = float(dark.mean() * 100.0) if dark.size else 0.0

    # 形态学在 2x 下采样上进行（比不随分辨率变化，速度 4x）
    ink_s = ink[::2, ::2]
    dark_s = dark[::2, ::2]
    gray_s = crop[::2, ::2]
    if dark_raw >= params.dark_pct_content * 2:
        # 深色明显 → 直接内容，跳过形态学
        ink_ratio, dark_ratio, bbox_frac, grad_ratio = ink_raw, dark_raw, 1.0, _grad_ratio(gray_s)
    else:
        ink_clean = _morph_clean(ink_s)
        dark_clean = _morph_clean(dark_s)
        ink_ratio = float(ink_clean.mean() * 100.0) if ink_clean.size else 0.0
        dark_ratio = float(dark_clean.mean() * 100.0) if dark_clean.size else 0.0
        ys, xs = np.nonzero(ink_clean)
        bbox_frac = 0.0
        if ink_clean.size and ys.size:
            area = float((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))
            bbox_frac = area / ink_clean.size
        grad_ratio = _grad_ratio(gray_s)

    # 边缘条带（原图最外圈 1.5%）：仅展示，不参与判定
    h, w = gray.shape
    strip = max(2, int(min(h, w) * 0.015))
    if strip * 2 < min(h, w):
        top = gray[:strip, :]
        bottom = gray[h - strip :, :]
        left = gray[strip : h - strip, :strip]
        right = gray[strip : h - strip, w - strip :]
        edge = np.concatenate([top.ravel(), bottom.ravel(), left.ravel(), right.ravel()])
        edge_ink = float((edge < thresh).mean() * 100.0)
    else:
        edge_ink = 0.0

    return {
        "mean": round(mean, 1),
        "ink_ratio": round(ink_ratio, 4),
        "dark_ratio": round(dark_ratio, 4),
        "ink_raw": round(ink_raw, 4),
        "dark_raw": round(dark_raw, 4),
        "grad_ratio": round(grad_ratio, 3),
        "bbox_frac": round(bbox_frac, 4),
        "edge_ink": round(edge_ink, 2),
    }


def classify(signals: dict, params: DetectParams, has_text: bool = False) -> BlankResult:
    """三级判定。浅色印章/签名/水印等模糊页面落入 MAYBE，绝不自动判空白。"""
    if has_text:
        return BlankResult(verdict=CONTENT, confidence=1.0, signals=signals)

    ink = signals["ink_ratio"]
    dark = signals["dark_ratio"]
    bbox = signals["bbox_frac"]
    grad = signals["grad_ratio"]

    if (
        dark <= params.dark_pct_blank
        and ink <= params.ink_threshold_pct
        and grad <= params.grad_blank_max_pct
    ):
        # 空白：置信度随墨迹比升高而下降
        conf = max(0.6, 1.0 - 0.8 * (ink / max(params.ink_threshold_pct, 1e-6)))
        return BlankResult(verdict=BLANK, confidence=round(min(conf, 0.99), 3), signals=signals)

    if (
        dark >= params.dark_pct_content
        or ink >= params.maybe_ink_max_pct
        or bbox >= params.bbox_frac_content
        or grad >= params.grad_content_min_pct
    ):
        conf = min(0.99, 0.7 + 0.3 * min(ink / 3.0, 1.0))
        return BlankResult(verdict=CONTENT, confidence=round(conf, 3), signals=signals)

    return BlankResult(verdict=MAYBE, confidence=round(0.5, 3), signals=signals)


def detect_page(gray: np.ndarray, params: DetectParams, has_text: bool = False) -> BlankResult:
    return classify(analyze_gray(gray, params), params, has_text=has_text)


def detect_document(
    renderer,
    n_pages: int,
    params: DetectParams,
    has_text_fn,
    progress_cb=None,
    cancel_cb=None,
) -> list[BlankResult]:
    """对整份文档逐页检测。progress_cb(done, total)；cancel_cb()->bool。"""
    results: list[BlankResult] = []
    for i in range(n_pages):
        if cancel_cb and cancel_cb():
            break
        img = renderer.render_detect(i)
        gray = np.asarray(img.convert("L"))
        try:
            has_text = bool(has_text_fn(i))
        except Exception:
            has_text = False
        results.append(detect_page(gray, params, has_text=has_text))
        if progress_cb:
            progress_cb(i + 1, n_pages)
    return results
