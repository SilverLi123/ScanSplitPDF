"""生成模拟扫描 PDF 与文本 PDF（全部为虚构内容，不涉及任何真实工作文件）。

图像页以 JPEG 嵌入（/DCTDecode），与真实扫描件结构一致；
文本页使用标准 Helvetica（无需嵌入字体），用于测试文本层提取路径。
"""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

PAGE_W, PAGE_H = 1240, 1754  # A4 @ 150 dpi
LAND_W, LAND_H = PAGE_H, PAGE_W  # 横向页面

_FONT_CACHE: dict[int, ImageFont.ImageFont] = {}


def _font(size: int):
    if size not in _FONT_CACHE:
        try:
            _FONT_CACHE[size] = ImageFont.load_default(size=size)
        except TypeError:
            _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def blank_page(seed: int = 0) -> Image.Image:
    return Image.new("RGB", (PAGE_W, PAGE_H), "white")


def shade_page(seed: int = 1) -> Image.Image:
    """有扫描阴影/轻微灰底的空白页。"""
    rng = _rng(seed)
    img = Image.new("L", (PAGE_W, PAGE_H), 246)
    d = ImageDraw.Draw(img)
    for _ in range(6):
        cx, cy = rng.randint(0, PAGE_W), rng.randint(0, PAGE_H)
        r = rng.randint(200, 500)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rng.randint(215, 232))
    img = img.filter(ImageFilter.GaussianBlur(60))
    return img.convert("RGB")


def noise_page(seed: int = 2) -> Image.Image:
    """只有少量噪点的空白页。"""
    rng = _rng(seed)
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    for _ in range(180):
        x, y = rng.randint(0, PAGE_W - 1), rng.randint(0, PAGE_H - 1)
        v = rng.randint(90, 170)
        d.rectangle([x, y, x + rng.randint(0, 2), y + rng.randint(0, 2)], fill=(v, v, v))
    return img


def edge_lines_page(seed: int = 3) -> Image.Image:
    """扫描边缘黑线的空白页。"""
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, PAGE_W - 1, 3], fill="black")
    d.rectangle([0, PAGE_H - 4, PAGE_W - 1, PAGE_H - 1], fill="black")
    d.rectangle([0, 0, 3, PAGE_H - 1], fill=(60, 60, 60))
    d.rectangle([PAGE_W - 4, 0, PAGE_W - 1, PAGE_H - 1], fill=(60, 60, 60))
    return img


def backside_page(seed: int = 4) -> Image.Image:
    """双面扫描留下的空白背面：均匀浅灰 + 极淡斑驳。"""
    rng = _rng(seed)
    img = Image.new("L", (PAGE_W, PAGE_H), 228)
    d = ImageDraw.Draw(img)
    for _ in range(300):
        x, y = rng.randint(0, PAGE_W - 1), rng.randint(0, PAGE_H - 1)
        d.point((x, y), fill=rng.randint(200, 235))
    img = img.filter(ImageFilter.GaussianBlur(2))
    return img.convert("RGB")


def stamp_page(seed: int = 5) -> Image.Image:
    """含浅色印章与签名的页面（必须保守，不可判空白）。"""
    rng = _rng(seed)
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    stamp = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.ellipse([10, 10, 390, 390], outline=(215, 90, 80, 150), width=8)
    sd.ellipse([40, 40, 360, 360], outline=(215, 90, 80, 110), width=4)
    for i in range(5):
        r = 150 - i * 26
        sd.ellipse([200 - r, 200 - r, 200 + r, 200 + r], outline=(215, 90, 80, 120), width=3)
    img.paste(stamp, (420, 900), stamp)
    d = ImageDraw.Draw(img)
    # 浅色签名笔迹
    x, y = 500, 1500
    for _ in range(70):
        d.line([x, y, x + rng.randint(-25, 25), y + rng.randint(-30, 5)],
               fill=(175, 175, 175), width=rng.randint(1, 3))
        x += rng.randint(-15, 25)
        y += rng.randint(-8, 8)
    return img


def _draw_text_block(
    d: ImageDraw.ImageDraw,
    lines: list[str],
    x0: int,
    y0: int,
    size: int,
    line_h: int,
    max_w: int,
) -> int:
    """画多行黑色文本，返回结束 y。"""
    y = y0
    for line in lines:
        d.text((x0, y), line, fill="black", font=_font(size))
        y += line_h
        if y - y0 > max_w:
            break
    return y


def text_page(seed: int = 10, landscape: bool = False, title: str = "REPORT") -> Image.Image:
    """普通文字页（虚构报告内容）。"""
    rng = _rng(seed)
    w, h = (LAND_W, LAND_H) if landscape else (PAGE_W, PAGE_H)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.text((w // 2 - 150, 120), title, fill="black", font=_font(64))
    d.line([80, 220, w - 80, 220], fill="black", width=4)
    y = 300
    for _ in range(18):
        words = " ".join(
            "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 9)))
            for _ in range(10)
        )
        d.text((90, y), words, fill="black", font=_font(36))
        y += 70
    return img


def table_page(seed: int = 20) -> Image.Image:
    """横向表格页（虚构数据）。"""
    rng = _rng(seed)
    img = Image.new("RGB", (LAND_W, LAND_H), "white")
    d = ImageDraw.Draw(img)
    d.text((LAND_W // 2 - 200, 60), "TABLE", fill="black", font=_font(56))
    x0, y0, x1, y1 = 60, 160, LAND_W - 60, LAND_H - 60
    rows, cols = 24, 12
    cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
    for i in range(rows + 1):
        y = y0 + i * ch
        d.line([x0, y, x1, y], fill="black", width=2)
    for j in range(cols + 1):
        x = x0 + j * cw
        d.line([x, y0, x, y1], fill="black", width=2)
    for i in range(rows):
        for j in range(cols):
            if rng.random() < 0.35:
                d.text((x0 + j * cw + 12, y0 + i * ch + 6),
                       str(rng.randint(0, 999)), fill="black", font=_font(26))
    return img


PAGE_BUILDERS = {
    "blank": blank_page,
    "shade": shade_page,
    "noise": noise_page,
    "edge_lines": edge_lines_page,
    "backside": backside_page,
    "stamp": stamp_page,
    "text": text_page,
    "table": table_page,
}


def make_page(kind: str, seed: int = 0, **kw) -> Image.Image:
    return PAGE_BUILDERS[kind](seed, **kw)


# -- PDF 组装 ----------------------------------------------------------------


def _add_image_page(writer: PdfWriter, img: Image.Image) -> None:
    w_pt = img.width / 150.0 * 72.0
    h_pt = img.height / 150.0 * 72.0
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=95)
    page = writer.add_blank_page(width=w_pt, height=h_pt)
    xobj = DecodedStreamObject()
    xobj.set_data(buf.getvalue())
    xobj.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(img.width),
            NameObject("/Height"): NumberObject(img.height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/DCTDecode"),
        }
    )
    img_ref = writer._add_object(xobj)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): img_ref})}
    )
    cs = DecodedStreamObject()
    cs.set_data(f"q {w_pt:.2f} 0 0 {h_pt:.2f} 0 0 cm /Im0 Do Q".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(cs)


def build_scan_pdf(path: str | Path, kinds: list[str], seeds: list[int] | None = None) -> Path:
    """按 kinds 列表逐页生成模拟扫描 PDF。"""
    path = Path(path)
    writer = PdfWriter()
    seeds = seeds or list(range(len(kinds)))
    for kind, seed in zip(kinds, seeds):
        _add_image_page(writer, make_page(kind, seed))
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _escape_pdf_text(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_text_pdf(
    path: str | Path, pages: list[list[tuple[str, float, float, float]]]
) -> Path:
    """生成带文本层的 PDF。pages: 每页 [(text, font_size, x, y), ...]，y 为 PDF 坐标。"""
    path = Path(path)
    writer = PdfWriter()
    for lines in pages:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        ops = [
            f"BT /F1 {size} Tf {x} {y} Td ({_escape_pdf_text(text)}) Tj ET"
            for text, size, x, y in lines
        ]
        cs = DecodedStreamObject()
        cs.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(cs)
    with open(path, "wb") as f:
        writer.write(f)
    return path
