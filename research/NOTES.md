# 参考项目调研笔记（2026-10-09）

## 1. bryanssss/pdf-split-and-rename（MIT）

- 形态：**浏览器 PWA**（app.js / index.html / manifest.webmanifest），非桌面应用。
- 拆分依据：文本区域/ID 变化、关键词、固定页数——**不是空白页拆分**。
- 离线问题：README 明示「下载 pinned JavaScript libraries from jsDelivr」「可选 OCR 下载 Tesseract 语言模型」→ 运行时依赖网络，违反本项目严格离线要求。
- 保真问题：浏览器端 PDF 库（pdf-lib 类）通常整体重写文档结构，无法保证不重编码扫描图像。
- 可借鉴（仅 UX 思路）：文件名模板占位符、导出前可编辑文件名、重名/空名警告、分组延续（carry-forward）概念。

**结论：不适合作为基础。** 浏览器方案无法满足「严格离线 + Windows 独立交付 + 原内容保真」三项硬指标。

## 2. The-Free-Converter/scan-pdf-cleaner（MIT）

- 技术栈：pypdf + pypdfium2 + numpy（与本项目选型一致）。
- 空白检测：72 DPI 渲染 → `gray < 240` 计为非白像素 → 占比 ≤ 0.5% 判空白。
  - 缺点：单一全局阈值，正是任务书禁止的做法；对阴影/边缘黑线/浅色印章无区分度，无置信度输出。
- 保真路径正确：`writer.add_page(reader.pages[i])` 复制页面对象，不重编码。本项目沿用该思路。
- 代码量小（99 行核心），质量一般，**检测算法不可直接复用，但验证了技术栈可行性**。

## 3. pdfarranger/pdfarranger（GPL-3.0）

- 形态：Python + GTK3 桌面应用，pikepdf 前端，功能成熟（缩略图、拖拽排序、旋转、裁剪）。
- **许可证：GPL-3.0 → 不复制任何代码**，仅参考交互设计：
  - 缩略图网格 + 多选 + 右键操作
  - 页面级旋转即时预览
  - 虚拟滚动思路（大文档性能）
- Windows 打包需 MSYS2 + GTK3 + cx_Freeze，工具链极重 → 进一步验证 Tkinter 选型正确。

## 4. baltpeter/scanprep（MIT）

- 空白检测算法（借鉴来源，本项目实现时做扩展并注明出处）：
  1. 自适应阈值：`mean - 50` 二值化（优于固定阈值，抗扫描灰底）
  2. 裁剪左右 10%、上下 5%（消除装订孔、扫描边缘黑线）
  3. MaxFilter(1) + MinFilter(3) 形态学去噪（消除少量噪点）
  4. 墨迹像素比 < 0.5% 判空白
- 分隔页：**条形码分隔纸**——任务书明确不要求用户使用特殊分隔纸，弃用该方案，但借鉴其分组数据结构（页面号列表 + 过滤空组）。
- **依赖 PyMuPDF（AGPL-3.0）→ 本项目改用 pypdfium2（BSD-3/Apache-2.0）规避**。

## 5. 最终技术选型

| 组件 | 用途 | 许可证 |
|---|---|---|
| Python 3.14 + Tkinter/ttk | 桌面 UI，随解释器自带，PyInstaller 打包干净 | PSF |
| pypdf | 页面复制拆分、/Rotate 旋转、文本提取 | BSD-3 |
| pypdfium2 | PDFium 渲染（缩略图/预览/空白检测用图），单 wheel 无外部 DLL | BSD-3 + Apache-2.0 |
| Pillow | 图像预处理、形态学 | MIT-CMU |
| numpy | 像素统计 | BSD-3 |
| PyInstaller | 打包 Windows EXE | GPL with linking exception（仅构建工具，产物不复制 GPL 代码）|

理由：
1. **离线**：全部为本地库，无任何网络调用、CDN、遥测。
2. **保真**：pypdf `add_page` 复制页面对象，保留内容流与嵌入图像原始字节；旋转仅写 `/Rotate` 属性，不碰内容流；缩略图渲染仅用于预览。
3. **简单可维护**：Tkinter 无额外 GUI 运行时（对比 GTK/MSYS2）；核心逻辑与 UI 完全分离。
4. **许可证洁净**：运行时依赖全部宽松许可证；pdfarranger（GPL-3）仅作交互参考，未复制代码。

## 6. 相对参考项目的补充能力

- 多信号空白检测 + 置信度 + 三级判定（空白/疑似/内容），阈值可调，疑似项必须人工确认
- 空白页 = 候选分隔符而非直接删除；文档内部空白页可保留
- 分组结构：显式分隔页集合 → 组，支持手动增删移动拆分点
- 输出前全量校验：内容流/嵌入图像哈希、页码映射、源文件 SHA-256 不变
- 单位历史记录、文件名模板、重名保护
