"""生成 RapidOCR 模型文件 SHA-256 清单（scansplit/ocr_model_manifest.json）。

在安装依赖后运行一次，随源码/安装包分发；运行时校验模型完整性。
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODEL_FILES = [
    "models/ch_PP-OCRv3_det_infer.onnx",
    "models/ch_PP-OCRv3_rec_infer.onnx",
    "models/ch_ppocr_mobile_v2.0_cls_infer.onnx",
    "config.yaml",
]


def main() -> None:
    from scansplit.ocr import model_dir

    base = model_dir()
    if base is None:
        print("错误：未安装 rapidocr_onnxruntime，无法生成清单。")
        return
    files = {}
    for rel in MODEL_FILES:
        f = base / rel
        if not f.exists():
            print(f"错误：模型文件不存在 {f}")
            return
        h = hashlib.sha256()
        with open(f, "rb") as fp:
            for block in iter(lambda: fp.read(1024 * 1024), b""):
                h.update(block)
        files[rel] = h.hexdigest()
    manifest = {
        "engine": "rapidocr_onnxruntime",
        "note": "PaddleOCR 官方 PP-OCRv3 中文模型（ONNX），运行时绝不联网下载",
        "files": files,
    }
    out = Path(__file__).resolve().parents[1] / "scansplit" / "ocr_model_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
