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


def _contents_bytes(page, errors: list[str] | None = None) -> bytes:
    """页面全部内容流的解码字节（/Contents 为数组时按顺序拼接）。

    读取异常时记录到 errors（fail-closed：调用方据此判失败），绝不静默放行。
    """
    c = page.get("/Contents", None)
    if c is None:
        return b""
    try:
        if isinstance(c, ArrayObject):
            return b"".join(c[i].get_data() for i in range(len(c)))
        return c.get_data()
    except Exception as e:
        if errors is not None:
            errors.append(f"内容流读取异常：{e}")
        return b""


def _raw_stream_bytes(obj, errors: list[str] | None = None) -> bytes | None:
    """流的原始编码字节；不可得时记录校验失败。"""
    try:
        target = obj.get_object() if hasattr(obj, "get_object") else obj
        raw = getattr(target, "_data", None)
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if errors is not None:
            errors.append("无法读取原始编码流，不能确认未重编码")
    except Exception as e:
        if errors is not None:
            errors.append(f"原始流读取异常：{e}")
    return None


def _contents_raw_sha(page, errors: list[str] | None = None) -> str | None:
    """内容流原始编码字节的哈希；任何组成流不可得时返回 None。"""
    c = page.get("/Contents", None)
    if c is None:
        return _sha(b"")
    try:
        if isinstance(c, ArrayObject):
            parts = []
            for i in range(len(c)):
                raw = _raw_stream_bytes(c[i], errors)
                if raw is None:
                    return None
                parts.append(raw)
            return _sha(b"".join(parts))
        raw = _raw_stream_bytes(c, errors)
        return _sha(raw) if raw is not None else None
    except Exception as e:
        if errors is not None:
            errors.append(f"内容流原始字节读取异常：{e}")
        return None


def _collect_images(res, errors: list[str] | None = None, depth: int = 0, seen: set | None = None) -> list[bytes]:
    """收集页面资源里的嵌入图像原始字节（递归 Form XObject，深度受限）。

    读取异常记录到 errors（fail-closed），不再静默跳过。
    """
    out: list[bytes] = []
    if res is None:
        return out
    if depth > 32:
        if errors is not None:
            errors.append("Form 嵌套超过校验深度，无法确认保真")
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
        except Exception as e:
            if errors is not None:
                errors.append(f"图像资源读取异常：{e}")
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
            except Exception as e:
                if errors is not None:
                    errors.append(f"图像解码读取异常：{e}")
        elif subtype == "/Form":
            try:
                out.extend(_collect_images(obj.get("/Resources"), errors, depth + 1, seen))
            except Exception as e:
                if errors is not None:
                    errors.append(f"Form 资源读取异常：{e}")
    return out


def _image_raw_shas(res, errors: list[str] | None = None, depth: int = 0, seen: set | None = None) -> list[str] | None:
    """嵌入图像原始编码字节哈希列表；任何图像原始流不可得时返回 None。"""
    out: list[str] = []
    if res is None:
        return out
    if depth > 32:
        if errors is not None:
            errors.append("Form 嵌套超过校验深度，无法确认原始编码流")
        return None
    if seen is None:
        seen = set()
    try:
        xo = res.get("/XObject")
    except Exception as e:
        if errors is not None:
            errors.append(f"资源字典读取异常：{e}")
        return None
    if not xo:
        return out
    for name in list(xo.keys()):
        try:
            obj = xo[name].get_object()
        except Exception as e:
            if errors is not None:
                errors.append(f"图像资源读取异常：{e}")
            continue
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        try:
            subtype = obj.get("/Subtype")
        except Exception:
            subtype = None
        if subtype == "/Image":
            raw = _raw_stream_bytes(obj, errors)
            if raw is None:
                return None
            out.append(_sha(raw))
        elif subtype == "/Form":
            sub = _image_raw_shas(obj.get("/Resources"), errors, depth + 1, seen)
            if sub is None:
                return None
            out.extend(sub)
    return out


def page_fingerprint(page) -> dict:
    """页面的内容指纹：尺寸 + 解码内容流哈希 + 原始编码流哈希 + 嵌入图像哈希 + 读取异常。

    errors 非空表示存在读取异常（fail-closed：校验方应判失败）。
    """
    errors: list[str] = []
    try:
        mb = page.mediabox
        mediabox = [round(float(mb.left), 1), round(float(mb.bottom), 1),
                    round(float(mb.right), 1), round(float(mb.top), 1)]
    except Exception as e:
        mediabox = None
        errors.append(f"页面尺寸读取异常：{e}")
    try:
        res = page.get("/Resources", None)
    except Exception as e:
        res = None
        errors.append(f"资源字典读取异常：{e}")
    images = _collect_images(res, errors)
    image_raw_shas = _image_raw_shas(res, errors)
    contents_raw_sha = _contents_raw_sha(page, errors)
    try:
        cropbox = [float(v) for v in page.cropbox]
        rotation = int(page.rotation or 0) % 360
        user_unit = float(page.get("/UserUnit", 1))
    except Exception as e:
        cropbox, rotation, user_unit = None, None, None
        errors.append(f"页面几何读取异常：{e}")
    return {
        "mediabox": mediabox,
        "contents_sha": _sha(_contents_bytes(page, errors)),
        "contents_raw_sha": contents_raw_sha,
        "image_shas": [_sha(d) for d in images],
        "image_raw_shas": image_raw_shas,
        "image_count": len(images),
        "errors": errors,
        "cropbox": cropbox,
        "rotation": rotation,
        "user_unit": user_unit,
    }


def verify_mapping(src_path: str | Path, out_path: str | Path, page_map: list[int],
                   rotations: list[int] | None = None) -> list[str]:
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
        # fail-closed：任何读取异常直接判失败，绝不在信息不全时放行
        if f_src["errors"] or f_out["errors"]:
            detail = "；".join((f_src["errors"] + f_out["errors"])[:3])
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）内容流/图像资源读取异常，"
                f"无法确认保真：{detail}"
            )
            continue
        if f_src["contents_sha"] != f_out["contents_sha"]:
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）解码内容流不一致"
            )
        elif (
            f_src["contents_raw_sha"] is not None
            and f_out["contents_raw_sha"] is not None
            and f_src["contents_raw_sha"] != f_out["contents_raw_sha"]
        ):
            # 解码数据一致但原始编码流字节不同（如流被重新压缩/重组）
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）原始编码流字节不一致"
                "（解码数据一致，疑似被重编码）"
            )
        if f_src["image_count"] != f_out["image_count"]:
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）嵌入图像数量不一致"
                f"（源 {f_src['image_count']}，输出 {f_out['image_count']}）"
            )
        elif sorted(f_src["image_shas"]) != sorted(f_out["image_shas"]):
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）解码图像数据不一致"
            )
        elif (
            f_src["image_raw_shas"] is not None
            and f_out["image_raw_shas"] is not None
            and sorted(f_src["image_raw_shas"]) != sorted(f_out["image_raw_shas"])
        ):
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）嵌入图像原始编码流不一致"
                "（解码数据一致，疑似被重编码）"
            )
        if f_src["mediabox"] != f_out["mediabox"] and f_src["mediabox"] is not None:
            issues.append(
                f"第 {out_idx + 1} 页（源第 {src_page} 页）页面尺寸不一致"
            )
        if f_src["cropbox"] != f_out["cropbox"] or f_src["user_unit"] != f_out["user_unit"]:
            issues.append(f"第 {out_idx + 1} 页（源第 {src_page} 页）裁切范围或页面比例不一致")
        expected_rotation = rotations[out_idx] if rotations is not None else f_src["rotation"]
        if rotations is not None and f_out["rotation"] != expected_rotation % 360:
            issues.append(f"第 {out_idx + 1} 页（源第 {src_page} 页）导出方向不一致")
    return issues
