"""保真校验：源文件哈希、页面内容流与嵌入图像哈希、页面映射关系。

拆分导出的页面必须与原 PDF 对应页面的内容流字节、嵌入图像字节完全一致
（旋转仅体现在 /Rotate 属性上，不改变内容）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import ArrayObject


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contents_bytes(page) -> bytes:
    """页面全部内容流的原始字节（/Contents 为数组时按顺序拼接）。"""
    c = page.get("/Contents", None)
    if c is None:
        return b""
    try:
        if isinstance(c, ArrayObject):
            return b"".join(c[i].get_data() for i in range(len(c)))
        return c.get_data()
    except Exception:
        return b""


def _collect_images(res, depth: int = 0, seen: set | None = None) -> list[bytes]:
    """收集页面资源里的嵌入图像原始字节（递归 Form XObject，深度受限）。"""
    out: list[bytes] = []
    if res is None or depth > 3:
        return out
    if seen is None:
        seen = set()
    xo = res.get("/XObject")
    if not xo:
        return out
    names = list(xo.keys())
    for name in names:
        try:
            obj = xo[name].get_object()
        except Exception:
            continue
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        try:
            subtype = obj.get("/Subtype")
        except Exception:
            subtype = None
        if subtype == "/Image":
            try:
                out.append(obj.get_data())
            except Exception:
                pass
        elif subtype == "/Form":
            try:
                out.extend(_collect_images(obj.get("/Resources"), depth + 1, seen))
            except Exception:
                pass
    return out


def page_fingerprint(page) -> dict:
    """页面的内容指纹：尺寸 + 内容流哈希 + 嵌入图像哈希列表。"""
    try:
        mb = page.mediabox
        mediabox = [round(float(mb.left), 1), round(float(mb.bottom), 1),
                    round(float(mb.right), 1), round(float(mb.top), 1)]
    except Exception:
        mediabox = None
    images = _collect_images(page.get("/Resources", None))
    return {
        "mediabox": mediabox,
        "contents_sha": _sha(_contents_bytes(page)),
        "image_shas": [_sha(d) for d in images],
        "image_count": len(images),
    }


def verify_mapping(src_path: str | Path, out_path: str | Path, page_map: list[int]) -> list[str]:
    """校验输出文件每页与源页面的映射保真。page_map: 输出每页对应的源页码（1 起始）。

    返回问题列表；空列表表示全部一致。
    """
    src_path, out_path = Path(src_path), Path(out_path)
    try:
        src = PdfReader(str(src_path))
    except Exception as e:
        return [f"无法重新打开源文件：{e}"]
    try:
        out = PdfReader(str(out_path))
    except Exception as e:
        return [f"无法打开输出文件：{e}"]

    if len(out.pages) != len(page_map):
        return [f"输出页数 {len(out.pages)} 与预期 {len(page_map)} 不一致"]

    issues: list[str] = []
    for out_idx, src_page in enumerate(page_map):
        try:
            f_src = page_fingerprint(src.pages[src_page - 1])
            f_out = page_fingerprint(out.pages[out_idx])
        except Exception as e:
            issues.append(f"第 {out_idx + 1} 页（源第 {src_page} 页）指纹计算失败：{e}")
            continue
        if f_src["contents_sha"] != f_out["contents_sha"]:
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）内容流不一致"
            )
        if f_src["image_count"] != f_out["image_count"]:
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）嵌入图像数量不一致"
                f"（源 {f_src['image_count']}，输出 {f_out['image_count']}）"
            )
        elif sorted(f_src["image_shas"]) != sorted(f_out["image_shas"]):
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）嵌入图像数据不一致"
            )
        if f_src["mediabox"] != f_out["mediabox"] and f_src["mediabox"] is not None:
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）页面尺寸不一致"
            )
    return issues
