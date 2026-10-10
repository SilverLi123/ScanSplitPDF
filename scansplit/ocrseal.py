"""公章（红色印章）文字提取：红色像素隔离 + 圆环极坐标展开 + OCR。

公章红墨（R 高、G/B 低）可与黑色正文干净分离；上弧单位名沿圆环排列，
常规 OCR 无法识别弧形文字，故把圆环带极坐标展开成直条再识别（正/反两个
方向各试一次，取效果好的）。
所有候选均来自 OCR 实际识别文字；识别失败返回空，绝不编造。
"""

from __future__ import annotations

import math
import re

import cv2
import numpy as np
from PIL import Image

from .ocr import OcrEngine, OcrLine
from .ocrfields import FieldCandidate, UNIT_SUFFIX

SEAL_CONF_CAP = 0.7
MIN_RED_FRAC = 0.0005  # 页面红色占比下限（防对零星红点做 OCR）
OUTER_BAND_DENSITY = 0.15  # 圆环验证：外环带红色占比下限
# 印章文字：4-30 个非标点字符，以单位后缀结尾；排除数字/字母（编号行）
SEAL_TEXT_RE = re.compile(
    rf"^[^\s，。；：、·*★0-9a-zA-Z]{{4,30}}(?:{UNIT_SUFFIX})$"
)


def _red_mask(img: Image.Image) -> np.ndarray:
    """红色像素掩码（红墨水：R 显著高于 G/B）。"""
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mask = (r > 110) & (r - g > 30) & (r - b > 30)
    mask = mask.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return mask


def _seal_ring(mask: np.ndarray) -> tuple[float, float, float] | None:
    """最大红色连通域的外接圆（圆心、半径）；不像圆环则返回 None。"""
    h, w = mask.shape
    red_frac = float((mask > 0).sum()) / (w * h)
    if red_frac < MIN_RED_FRAC:
        return None
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    largest = max(cnts, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    if area < 0.0005 * w * h or area > 0.35 * w * h:
        return None
    (cx, cy), R = cv2.minEnclosingCircle(largest)
    m = min(w, h)
    if not (0.03 * m <= R <= 0.35 * m):
        return None
    # 外环带 [0.85R, R] 红色占比：公章外圈是实线圆环，横排红字（红头）不会形成
    ys, xs = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    band = (dist >= 0.85 * R) & (dist <= R)
    density = float((mask[band] > 0).sum()) / max(int(band.sum()), 1)
    if density < OUTER_BAND_DENSITY:
        return None
    # 内带不应是实心红盘（排除纯红圆点/色块）
    inner = (dist >= 0.55 * R) & (dist <= 0.85 * R)
    inner_density = float((mask[inner] > 0).sum()) / max(int(inner.sum()), 1)
    if inner_density > 0.9:
        return None
    return cx, cy, R


def _unwrap_strip(mask: np.ndarray, cx: float, cy: float, R: float) -> Image.Image:
    """把圆环带 [0.55R, 0.97R] 极坐标展开成直条（黑字白底 PIL 图）。

    θ 从 π（左点）起绕一圈；上弧文字展开后为正立、下弧文字为倒置，
    调用方对正/反两方向分别识别。"""
    r_in, r_out = 0.55 * R, 0.97 * R
    W = min(int(2 * math.pi * R), 4000)
    H = max(int(r_out - r_in), 24)
    theta = np.linspace(math.pi, 3 * math.pi, W, endpoint=False)
    r = np.linspace(r_in, r_out, H)
    Theta, Rr = np.meshgrid(theta, r)
    map_x = (cx + Rr * np.cos(Theta)).astype(np.float32)
    map_y = (cy + Rr * np.sin(Theta)).astype(np.float32)
    strip = cv2.remap(mask, map_x, map_y, cv2.INTER_LINEAR)
    # 太窄时放大到 128px 高（保证字形足够大供 OCR）
    if H < 128:
        strip = cv2.resize(strip, (W * 128 // H, 128), interpolation=cv2.INTER_CUBIC)
    return Image.fromarray(255 - strip).convert("RGB")


def _region_crop(mask: np.ndarray, cx: float, cy: float, R: float) -> Image.Image:
    """印章外接方框内的红墨区域（横排文字用），黑字白底。"""
    h, w = mask.shape
    pad = int(R * 0.08)
    x0, y0 = max(int(cx - R) - pad, 0), max(int(cy - R) - pad, 0)
    x1, y1 = min(int(cx + R) + pad, w), min(int(cy + R) + pad, h)
    crop = mask[y0:y1, x0:x1]
    ch, cw = crop.shape
    if cw < 800:
        crop = cv2.resize(crop, (800, max(int(800 * ch / cw), 24)),
                          interpolation=cv2.INTER_CUBIC)
    return Image.fromarray(255 - crop).convert("RGB")


def _seal_candidate(text: str, lines: list[OcrLine], reason: str) -> FieldCandidate:
    avg = sum(l.score for l in lines) / max(len(lines), 1)
    conf = min(round(avg * 0.85, 3), SEAL_CONF_CAP)
    return FieldCandidate(text, conf, "印章", reason=reason)


def _strip_candidates(strip: Image.Image, engine) -> list[FieldCandidate]:
    out: list[FieldCandidate] = []
    for variant in (strip, strip.transpose(Image.FLIP_TOP_BOTTOM)):
        lines = engine.recognize(variant)
        if not lines:
            continue
        avg_h = max(1.0, sum(l.height for l in lines) / len(lines))
        rows: dict[int, list[OcrLine]] = {}
        for l in lines:
            rows.setdefault(int(l.cy / (avg_h * 1.5)), []).append(l)
        for row in rows.values():
            text = "".join(l.text for l in sorted(row, key=lambda x: x.x0)).strip()
            if SEAL_TEXT_RE.match(text):
                out.append(_seal_candidate(text, row, "印章弧形文字展开识别"))
    return out


def extract_seal_units(img: Image.Image, engine=None) -> list[FieldCandidate]:
    """从页面图像中提取印章上的单位候选；无印章/识别失败返回空列表。"""
    eng = engine or OcrEngine.get()
    if eng is None or not eng.available:
        return []
    try:
        mask = _red_mask(img)
        ring = _seal_ring(mask)
        if ring is None:
            return []
        cx, cy, R = ring
        out: list[FieldCandidate] = []
        strip = _unwrap_strip(mask, cx, cy, R)
        out += _strip_candidates(strip, eng)
        crop = _region_crop(mask, cx, cy, R)
        for l in eng.recognize(crop):
            text = l.text.strip()
            if SEAL_TEXT_RE.match(text):
                out.append(_seal_candidate(text, [l], "印章区域横排文字"))
        # 去重：同文本保留置信度高者
        dedup: dict[str, FieldCandidate] = {}
        for c in out:
            cur = dedup.get(c.text)
            if cur is None or c.confidence > cur.confidence:
                dedup[c.text] = c
        return sorted(dedup.values(), key=lambda c: c.confidence, reverse=True)[:4]
    except Exception:
        return []
