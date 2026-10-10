"""本地 OCR 引擎封装（RapidOCR：PaddleOCR 官方 PP-OCR 模型 + ONNX Runtime 推理）。

选型说明：PaddleOCR 官方 pip 包依赖 PaddlePaddle 框架，截至 2026-10 无
Python 3.14 的 Windows 轮子；RapidOCR 直接运行 PaddleOCR 官方发布的
PP-OCRv3 中文检测/识别模型（ONNX 格式），许可证 Apache-2.0，模型随包分发。

约束（任务书硬性要求）：
- 完全离线：模型随包分发，运行时绝不联网下载，不调用任何云端 API；
- 启动时对模型文件做 SHA-256 完整性校验（与清单比对）；
- OCR 仅用于临时识别分析：输入是渲染图像，输出是文本与坐标，
  绝不写回 PDF、绝不重编码扫描图像；
- OCR 结果只是建议，绝不作为唯一可信依据。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

MANIFEST_NAME = "ocr_model_manifest.json"


def _engine_class():
    try:
        from rapidocr_onnxruntime import RapidOCR

        return RapidOCR
    except Exception:
        return None


def ocr_available() -> bool:
    """当前环境是否可用 OCR（依赖与模型齐全）。"""
    return _engine_class() is not None


def _alias_rapidocr_submodules() -> None:
    """rapidocr 通过 sys.path 附加包目录后，以 'ch_ppocr_v3_det' 等顶层名
    importlib.import_module 加载子模块；PyInstaller 冻结后磁盘上可能只有数据
    目录（无 __init__.py → 命名空间包）导致 AttributeError。提前把真实嵌套
    模块注册到顶层名（源码运行同样无害）。"""
    import importlib
    import sys

    for sub in ("ch_ppocr_v3_det", "ch_ppocr_v3_rec", "ch_ppocr_v2_cls"):
        try:
            pkg = importlib.import_module(f"rapidocr_onnxruntime.{sub}")
        except Exception:
            continue
        sys.modules[sub] = pkg
        for child in ("text_detect", "text_recognize", "text_cls", "utils"):
            try:
                sys.modules[f"{sub}.{child}"] = importlib.import_module(
                    f"rapidocr_onnxruntime.{sub}.{child}"
                )
            except Exception:
                pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest() -> dict:
    pkg = Path(__file__).parent / MANIFEST_NAME
    try:
        return json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def model_dir() -> Path | None:
    """RapidOCR 包内模型目录（随包分发）。"""
    try:
        import rapidocr_onnxruntime

        return Path(rapidocr_onnxruntime.__file__).parent
    except Exception:
        return None


def verify_models() -> tuple[bool, list[str]]:
    """模型完整性校验。返回 (是否通过, 问题列表)。"""
    base = model_dir()
    if base is None:
        return False, ["未安装 rapidocr_onnxruntime"]
    manifest = load_manifest()
    if not manifest.get("files"):
        return False, ["缺少模型清单文件"]
    issues = []
    for rel, sha in manifest["files"].items():
        f = base / rel
        if not f.exists():
            issues.append(f"模型文件缺失：{rel}")
            continue
        actual = _sha256_file(f)
        if actual != sha:
            issues.append(f"模型文件校验失败：{rel}")
    return not issues, issues


@dataclass
class OcrLine:
    text: str
    score: float
    box: list  # 4 点 [(x, y), ...]

    @property
    def xs(self) -> list[float]:
        return [float(p[0]) for p in self.box]

    @property
    def ys(self) -> list[float]:
        return [float(p[1]) for p in self.box]

    @property
    def x0(self) -> float:
        return min(self.xs)

    @property
    def y0(self) -> float:
        return min(self.ys)

    @property
    def x1(self) -> float:
        return max(self.xs)

    @property
    def y1(self) -> float:
        return max(self.ys)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0


class OcrEngine:
    """懒加载单例。首次 recognize 时加载模型（约 1-2 秒）。"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._engine = None
        self._error: str | None = None
        self._model_warned = False

    @classmethod
    def get(cls) -> "OcrEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure(self) -> None:
        if self._engine is not None or self._error is not None:
            return
        if not self._model_warned:
            self._model_warned = True
            ok, issues = verify_models()
            if not ok:
                self._error = "；".join(issues)
                return
        self._engine, self._error = _init_engine(use_angle_cls=True)

    @property
    def available(self) -> bool:
        self._ensure()
        return self._engine is not None

    @property
    def error(self) -> str | None:
        self._ensure()
        return self._error

    def recognize(self, img: Image.Image) -> list[OcrLine]:
        """识别一张 RGB 图像。失败返回空列表（绝不抛异常给调用方）。"""
        self._ensure()
        return _recognize_lines(self._engine, img)


class OcrEngineNoCls:
    """方向分析专用：禁用角度分类（cls）的 OCR 引擎。

    cls 模型会把 90/180/270 的文字自动翻转成可识别方向，导致常规引擎对
    四个方向的渲染都能读通——质量对比失去区分度。禁用 cls 后倒置文字
    识别质量骤降，0/180 与 90/270 即可由质量可靠区分。
    懒加载单例，仅在用户执行"识别方向"时创建。
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._engine = None
        self._error: str | None = None

    @classmethod
    def get(cls) -> "OcrEngineNoCls":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure(self) -> None:
        if self._engine is not None or self._error is not None:
            return
        self._engine, self._error = _init_engine(use_angle_cls=False)

    @property
    def available(self) -> bool:
        self._ensure()
        return self._engine is not None

    @property
    def error(self) -> str | None:
        self._ensure()
        return self._error

    def recognize(self, img: Image.Image) -> list[OcrLine]:
        self._ensure()
        return _recognize_lines(self._engine, img)


def _init_engine(use_angle_cls: bool):
    cls = _engine_class()
    if cls is None:
        return None, "未安装本地 OCR 组件（rapidocr_onnxruntime）"
    try:
        _alias_rapidocr_submodules()
        return cls(use_angle_cls=use_angle_cls), None
    except Exception as e:
        return None, f"OCR 引擎初始化失败：{e}"


def _recognize_lines(engine, img: Image.Image) -> list[OcrLine]:
    if engine is None:
        return []
    try:
        import numpy as np

        arr = np.asarray(img.convert("RGB"))
        result, _ = engine(arr)
    except Exception:
        return []
    lines: list[OcrLine] = []
    for item in result or []:
        try:
            box, text, score = item[0], item[1], item[2]
            if not text or not str(text).strip():
                continue
            lines.append(
                OcrLine(text=str(text).strip(), score=float(score), box=box)
            )
        except (TypeError, ValueError, IndexError):
            continue
    return lines
