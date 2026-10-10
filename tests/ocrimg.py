"""生成中文公文模拟图像（使用 Windows 自带中文字体，全部为虚构内容）。"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simkai.ttf",
]

_FONT_CACHE: dict[tuple[str, int], ImageFont.ImageFont | None] = {}


def cjk_font_path() -> str | None:
    for p in CJK_FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _font(size: int, bold: bool = False):
    key = ("bold" if bold else "normal", size)
    if key not in _FONT_CACHE:
        path = cjk_font_path()
        if path is None:
            _FONT_CACHE[key] = None
        else:
            try:
                _FONT_CACHE[key] = ImageFont.truetype(path, size)
            except OSError:
                _FONT_CACHE[key] = None
    return _FONT_CACHE[key]


def fonts_available() -> bool:
    return cjk_font_path() is not None


def make_cn_document(
    title: str | list[str],
    body_lines: list[str] | None = None,
    unit: str | None = None,
    date: str = "2026年10月9日",
    header: str | None = None,
    size: tuple[int, int] = (1400, 1800),
    title_size: int = 64,
    body_size: int = 30,
    blur: float = 0.0,
    skew_deg: float = 0.0,
    landscape: bool = False,
    table_grid: bool = False,
) -> Image.Image:
    """生成一份虚构公文图像。

    title: 单行字符串或多行标题列表（多行时逐行居中拼接）。
    blur: 高斯模糊半径（模拟扫描模糊）。skew_deg: 倾斜角度（模拟扫描倾斜）。
    """
    w, h = size
    if landscape:
        w, h = h, w
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    if header:
        f = _font(body_size - 4)
        if f:
            d.text((60, 60), header, fill=(180, 30, 30), font=f)

    titles = [title] if isinstance(title, str) else title
    y = int(h * 0.16)
    for i, t in enumerate(titles):
        f = _font(title_size, bold=True)
        if f is None:
            break
        tw = d.textlength(t, font=f)
        d.text(((w - tw) / 2, y), t, fill="black", font=f)
        y += int(title_size * 1.6)
    y += int(body_size * 0.8)

    for line in body_lines or []:
        f = _font(body_size)
        if f is None:
            break
        d.text((80, y), line, fill="black", font=f)
        y += int(body_size * 1.9)

    if table_grid:
        x0, y0, x1, y1 = 60, y + 40, w - 60, h - 100
        rows, cols = 14, 8
        cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
        for i in range(rows + 1):
            d.line([x0, y0 + i * ch, x1, y0 + i * ch], fill="black", width=2)
        for j in range(cols + 1):
            d.line([x0 + j * cw, y0, x0 + j * cw, y1], fill="black", width=2)

    if unit:
        f = _font(int(body_size * 1.15))
        if f:
            uw = d.textlength(unit, font=f)
            d.text((w - uw - 120, h - int(body_size * 5.0)), unit, fill="black", font=f)
        f2 = _font(body_size - 4)
        if f2:
            dw = d.textlength(date, font=f2)
            d.text((w - dw - 120, h - int(body_size * 3.2)), date, fill="black", font=f2)

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if skew_deg:
        img = img.rotate(skew_deg, resample=Image.BICUBIC, expand=True,
                         fillcolor="white")
    return img


def make_seal(unit_arc: str = "", unit_flat: str = "", code: str = "",
              size: int = 600) -> Image.Image:
    """合成红色圆形公章（RGBA 白底红字，全部虚构内容）。

    unit_arc: 上弧一圈字（沿圆环排列、字顶朝外）；unit_flat: 下沿横排单位名；
    code: 印章编号。无中文字体时返回纯白图（调用方负责跳过）。
    """
    import math

    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    r = size * 0.42
    red = (210, 40, 40, 255)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=red, width=8)
    d.ellipse([cx - r + 14, cy - r + 14, cx + r - 14, cy + r - 14], outline=red, width=3)
    if unit_arc:
        f_arc = _font(44)
        if f_arc is None:
            return img
        arc_r = r - 46
        n = len(unit_arc)
        start_deg = 180 + (180 - (n - 1) * 16) / 2
        for i, ch in enumerate(unit_arc):
            deg = start_deg + i * 16
            ang = math.radians(deg)
            x = cx + arc_r * math.cos(ang)
            y = cy + arc_r * math.sin(ang)
            char_img = Image.new("RGBA", (70, 70), (0, 0, 0, 0))
            cd = ImageDraw.Draw(char_img)
            cd.text((35, 35), ch, fill=red, font=f_arc, anchor="mm")
            # 字顶朝外（径向向外）：θ 处需要旋转 270°−θ
            char_img = char_img.rotate(270 - deg, expand=True, resample=Image.BICUBIC)
            img.alpha_composite(char_img,
                                (int(x - char_img.width / 2), int(y - char_img.height / 2)))
    d.polygon(
        [(cx + 55 * math.cos(math.radians(a)), cy - 55 * math.sin(math.radians(a)))
         for a in range(90, 450, 72)],
        fill=red,
    )
    if unit_flat:
        f_flat = _font(40)
        if f_flat is not None:
            d.text((cx, cy + r - 80), unit_flat, fill=red, font=f_flat, anchor="mm")
    if code:
        f_code = _font(26)
        if f_code is not None:
            d.text((cx, cy + r - 30), code, fill=red, font=f_code, anchor="mm")
    return img
