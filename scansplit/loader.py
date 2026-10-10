"""打开与检查 PDF：加密、数字签名、表单、批注检测，返回明确警告。"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PasswordType


class LoadError(Exception):
    pass


def _has_signature(reader: PdfReader) -> bool:
    try:
        root = reader.trailer.get("/Root")
        if root is not None:
            acro = root.get("/AcroForm")
            if acro is not None:
                if acro.get("/SigFlags"):
                    return True
                fields = acro.get("/Fields")
                if fields:
                    for f in fields:
                        try:
                            if f.get_object().get("/FT") == "/Sig":
                                return True
                        except Exception:
                            continue
    except Exception:
        pass
    return False


def _count_annotations(reader: PdfReader) -> int:
    n = 0
    try:
        for page in reader.pages:
            annots = page.get("/Annots")
            if annots:
                n += len(annots)
    except Exception:
        pass
    return n


def open_document(path: str | Path) -> tuple[PdfReader, list[str]]:
    """打开 PDF 并返回 (reader, warnings)。失败抛 LoadError。"""
    path = Path(path)
    if not path.exists():
        raise LoadError(f"文件不存在：{path}")
    try:
        reader = PdfReader(str(path))
    except Exception as e:
        raise LoadError(f"无法打开 PDF（文件损坏或格式不支持）：{path.name}\n{e}") from e

    warnings: list[str] = []
    if reader.is_encrypted:
        try:
            result = reader.decrypt("")
        except Exception as e:
            raise LoadError(f"PDF 已加密，无法解密：{path.name}\n{e}") from e
        if result == PasswordType.NOT_DECRYPTED:
            raise LoadError(
                f"PDF 已加密且需要密码，本工具不支持处理带密码的 PDF：{path.name}\n"
                "请先去除密码后再导入。"
            )
        warnings.append("该 PDF 已加密（空密码解密成功）。导出会生成未加密的新文件。")

    if _has_signature(reader):
        warnings.append(
            "检测到数字签名。拆分导出会生成新文件，原数字签名将失效（源文件不会被修改）。"
        )
    n_annots = _count_annotations(reader)
    if n_annots:
        warnings.append(
            f"检测到 {n_annots} 个批注/表单元素。页面批注会随页面复制，"
            "但导出文件不保证原表单交互功能完整。"
        )
    if len(reader.pages) == 0:
        raise LoadError(f"PDF 没有任何页面：{path.name}")

    return reader, warnings
