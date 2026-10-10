# 第三方组件许可证说明

ScanSplit PDF 完全离线运行，不调用任何在线服务。以下为运行与打包所依赖的第三方组件及其许可证。
许可证全文随各组件分发包提供（见各组件 `LICENSE` 文件）。

## 运行时依赖（随程序分发）

| 组件 | 版本 | 许可证 | 用途 |
|------|------|--------|------|
| pypdf | 6.19.0 | BSD-3-Clause | PDF 读取、页面对象复制与导出（保真） |
| pypdfium2（含 PDFium） | 5.14.0 | BSD-3-Clause / Apache-2.0（PDFium 及其构建依赖见包内 BUILD_LICENSES） | 页面渲染（预览/空白检测/OCR 输入） |
| Pillow | 12.3.0 | MIT-CMU（HPND） | 图像处理 |
| numpy | 2.5.3 | BSD-3-Clause | 空白检测信号计算 |
| rapidocr-onnxruntime | 1.2.3 | Apache-2.0 | OCR 推理封装（本地，无网络） |
| PaddleOCR PP-OCRv3 模型（随 rapidocr 分发） | — | Apache-2.0 | 中文检测/识别/方向分类（.onnx） |
| onnxruntime | 1.31.0 | MIT | ONNX 模型推理引擎 |
| opencv-python | 5.0.0.93 | Apache-2.0 | OCR 图像预处理 |
| PyYAML | 6.0.3 | MIT | OCR 配置解析 |
| shapely | 2.2.0 | BSD-3-Clause（含 GEOS LGPL-2.1 组件） | OCR 后处理几何运算 |
| pyclipper | 1.4.0 | MIT | OCR 文本框裁剪 |
| six | 1.17.0 | MIT | 兼容层 |
| flatbuffers | 25.12.19 | Apache-2.0 | onnxruntime 依赖 |
| protobuf | 7.36.2 | BSD-3-Clause | onnxruntime 依赖 |
| colorama | 0.4.6 | BSD-3-Clause | 终端颜色（控制台日志） |
| packaging | 26.3 | Apache-2.0 / BSD-2-Clause | 版本解析 |

## 界面组件

| 组件 | 许可证 | 说明 |
|------|--------|------|
| Tcl/Tk（Python 标准库自带） | Tcl/Tk License（BSD 风格） | 图形界面 |

## 仅构建时使用（不随程序分发）

| 组件 | 版本 | 许可证 | 说明 |
|------|------|--------|------|
| PyInstaller | 6.22.3 | GPL-2.0-or-later（含 bootloader 例外条款） | 仅用于打包 EXE；bootloader 例外允许闭源分发产物 |
| pytest / pluggy / iniconfig / Pygments | — | MIT 等 | 仅用于开发测试，不随程序分发 |

## 参考项目（仅借鉴思路，未复制代码）

- scanprep（MIT）—— 空白页检测算法思路（自适应阈值+边缘裁剪+形态学），本项目以多信号模型重新实现；
- pdf-split-and-rename、scan-pdf-cleaner（MIT）—— 交互与产品思路参考；
- pdfarranger（GPL-3.0）—— 仅参考交互设计，未采用其代码（GPL 与本项目分发方式不兼容）。

## 模型分发说明

PP-OCRv3 中文模型（检测/识别/方向分类，ONNX 格式）随 `rapidocr-onnxruntime` 包分发，
程序启动时对本机模型文件做 SHA-256 完整性校验（清单见 `scansplit/ocr_model_manifest.json`），
运行时绝不联网下载模型，也不调用任何云端 API。
