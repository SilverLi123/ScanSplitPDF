# ScanSplit PDF — 项目交接文档（给接手的 Agent）

> 用途：让接手本项目的另一个 AI/开发者快速理解项目全貌、硬约束、架构、
> 历史决策与常见坑，避免重复踩坑。最后更新：2026-10-10（v1.5 之后）。

---

## 1. 项目一句话

Windows 10/11 **离线**桌面工具：把"多份公文连续扫描成一个 PDF"的文件按空白页拆分，
OCR 提取每份的标题与发文单位，按 `标题——单位.pdf` 命名并**无损**导出。

核心闭环：**空白页自动拆分 → 本地 OCR 自动命名 → 无损导出**。

- 源码：`D:\vibe-coding\scansplit-pdf`（本仓库）
- 用户使用的交付物：`D:\ScanSplitPDF\`（打包版 onedir 目录，每次改完重新打包替换）
- 真实测试样本（用户提供，非仓库文件）：
  `C:\Users\li333\Downloads\%E7%BA%AF%E5%9B%BE%E7%89%87PDF_%E5%A4%9A%E6%96%87%E6%A1%A3%E7%A9%BA%E7%99%BD%E9%A1%B5%E5%88%86%E9%9A%94_%E6%B5%8B%E8%AF%95%E6%A0%B7%E6%9C%AC.pdf`
  （文件名在磁盘上就是字面百分号编码；11 页，分隔页 [3,7,8,11]，分组 [(1,2),(4,6),(9,10)]；
  组 2 是侧放的横版表格，需旋转 270°）

---

## 2. 硬约束（任务书原话，最高优先级，任何改动不得违反）

1. **用户不允许程序擅自修改扫描件原有内容。**
2. **用户不希望上传工作文件，不允许在实际文档处理流程中调用在线 AI API。**
3. **绝对禁止对正式输出页面重新截图、重绘、压缩或重新编码。**
   导出只能复制 PDF 页面对象 + 写 `/Rotate` 属性，字节级保真（见 verify.py）。
4. **不覆盖或修改源 PDF。** 源文件在导入时只读打开，导出后 SHA-256 复检。
5. **OCR 只能用于临时识别和分析，不得把识别文字重新写入输出 PDF，也不得重新编码扫描图像。**
6. **运行时不允许自动联网下载模型或调用云端 API。** OCR 模型随包分发并做 SHA-256 完整性校验。
7. **严禁在测试或开发中覆盖用户原始文件。** 测试全部用合成扫描件（tests/samplegen.py、
   tests/ocrimg.py），只写临时目录。
8. **不要伪造标题。** 识别不出就留空并标"待确认"，候选必须全部来自 OCR 实际识别文字。
9. **不直接复制许可证不兼容的代码。** GPL-3 的 pdfarranger 只参考交互设计，未用其代码
   （选型与参考见 research/NOTES.md）。

违反上述任何一条的改动都不可接受——这是验收红线，不是风格偏好。

---

## 3. 环境与运行

| 项 | 值 |
|----|----|
| 系统 | Windows 11 Home China（用户机器），Python 3.14.3 x64 |
| 虚拟环境 | `D:\vibe-coding\scansplit-pdf\.venv`（依赖见 requirements.lock.txt） |
| 关键依赖 | pypdf 6.19（读写）、pypdfium2 5.14（渲染）、rapidocr_onnxruntime 1.2.3（OCR，官方 PP-OCRv3 模型）、opencv-python、numpy、PIL、PyInstaller 6.22.3（打包，仅构建时需要） |
| 运行 | `run.bat` 或 `.venv\Scripts\python -m scansplit.main` |
| 测试 | `.venv\Scripts\python -m pytest`（当前 100 passed，约 70s；无 OCR/无中文字体的机器上相关用例自动跳过） |
| 打包 | `build.bat`（PyInstaller onedir）→ `dist\ScanSplitPDF\ScanSplitPDF.exe` |
| 自检 | `dist\ScanSplitPDF\ScanSplitPDF.exe --selftest`（模型 SHA-256、检测/渲染依赖） |
| 部署 | `D:\ScanSplitPDF\`（先 taskkill 正在运行的 ScanSplitPDF.exe，再 robocopy 镜像；见 §9） |
| 设置文件 | `%APPDATA%\ScanSplitPDF\settings.json`（不在仓库） |

OCR 组件是本机已装的（RapidOCR + ONNX Runtime + 模型随仓库/包分发），
卸载/缺字体时 OCR 相关测试自动跳过，但功能会降级为"待确认"。

---

## 4. 架构与数据流

模块全部在 `scansplit/`（共约 6200 行）：

```
用户操作流：导入 → 检测空白 → 自动分组 → OCR 命名 → 旋转 → 导出
                ↓           ↓           ↓           ↓         ↓
模块：      loader     blankdetect  grouping   autoname   exporter
            render（渲染，贯穿始终，LRU 缓存）        + ocrfields/ocrseal/ocr
            model（状态 + 撤销快照）  rotation（/Rotate 管理）  naming（文件名）
```

各模块职责（改动前先读对应文件）：

| 文件 | 职责 | 注意点 |
|------|------|--------|
| `main.py` | 入口（含 `--selftest`） | 打包路径处理 |
| `loader.py` | 打开/检查 PDF | 加密（仅空密码）、签名、批注警告在这里 |
| `blankdetect.py` | 空白页检测 | 多信号（墨迹比/深色比/梯度比/包围盒）+ 三级判定（空白/疑似/有内容）；信号在形态学清洗后的 mask 上算；印章/浅签名 → 疑似（保守） |
| `grouping.py` | 分隔页 → 文档组 | 纯函数；连续空白合并、首尾空白不产生空组、内部空白保留 |
| `render.py` | 页面渲染 | PageRenderer，**必须在同一线程使用（Tk 主线程）**；LRU 缓存 key=(idx0, bucket, width, total_rot)；512MB 字节预算淘汰；`close()` 清缓存 |
| `ocr.py` | OCR 引擎封装 | OcrEngine（常规，含角度分类 cls）/ OcrEngineNoCls（use_angle_cls=False）；模型完整性校验；**cls 会把 90/180/270° 文字自动翻转识别**（方向分析的根源坑，见 §7） |
| `ocrfields.py` | 标题/单位字段提取 | 纯几何+格式启发式；标题多行拼接；单位分文头/落款/正文提及；LABELED_UNIT_RE 表格标注行；`merge_unit_candidates(first, last, seal)` |
| `ocrseal.py` | 公章（红章）文字提取 | 红色像素隔离 → 圆环检测 → 极坐标展开弧形字 → OCR；来源"印章"封顶 0.7 置信度 |
| `autoname.py` | 命名编排、方向分析、边界复核 | `_ocr_page_rotation_aware` 返回 (lines, w, h, img)；侧放页面自动按回正方向识别（**只用于识别，绝不自动改旋转**）；印章来源的 unit 强制 need_confirm |
| `rotation.py` | 旋转目标角度管理 | 只写 `/Rotate`；orientation_suggestions 跳过空白分隔页 |
| `naming.py` | 文件名模板/非法字符/防覆盖/单位历史 | 空名回退逻辑 |
| `exporter.py` | 批量导出 | 预检、逐份保真校验、取消、失败不影响其余 |
| `verify.py` | 保真校验 | 内容流哈希 + 图像哈希 + 页面映射；SHA-256 |
| `model.py` | 数据模型 + 撤销快照 | Workspace.snapshot/restore 是撤销最小单元；**Group 的候选字段有两种形态**（见 §7 坑 9） |
| `settings.py` | 设置持久化 | zoom/fit_mode 也要保存恢复 |
| `ui.py` | 主窗口编排 | 撤销/重做、进度对话框、对话框的 doc 绑定（防切换文档误操作，见 §7 坑 4）；v1.6 起工具栏按钮经 `self.tool_btns` 管理、`_update_step_state` 按工作流阶段置灰/启用，`direction_analyze_group` 支持组级方向分析 |
| `ui_widgets.py` | 缩略图条/分组面板/预览 | GroupPanel 按 **dict 风格**消费候选（`c.get("text")`）；缩略图滚动必须 redraw |

`tests/`：14 个测试文件，100 用例。合成数据生成：`tests/samplegen.py`（PDF 级）、
`tests/ocrimg.py`（图像级，含 `make_seal` 公章合成）。`tools/`：模型清单生成、
OCR 准确率报告。

---

## 5. 关键设计决策（为什么要这么设计）

1. **无损导出 = pypdf 页面对象级复制**，绝不用 pdfium 重渲染成图再拼 PDF。
   保真由 verify.py 用内容流+图像哈希逐页校验兜底。
2. **旋转只写 `/Rotate`**，不重绘。pypdf 的 `rotate()` 是累加语义（会改内容流），
   已弃用，直接设置页面 `/Rotate` 属性。
3. **OCR 引擎选 RapidOCR（ONNX Runtime 跑 PP-OCRv3 官方模型）**，因为 PaddlePaddle
   截至 2026-10 没有 Python 3.14 Windows 轮子。模型随包分发，完全离线。
4. **方向识别不用 cls 模型**：cls 会把侧放文字自动转正识别，导致 0/90/180/270
   四方向质量对比无区分度（用户实测横版表格方向全错）。改为：
   检测框横竖几何（`line_orientation`）定候选对 {0,180} 或 {90,270}，
   再用 OcrEngineNoCls 在候选对内做质量对比。
5. **撤销 = Workspace 全量快照**（JSON 可序列化 dict），不做命令模式——简单可靠。
6. **空白检测保守优先**：印章/浅签名判"疑似"由人确认，绝不武断删除。
7. **无落款不编造**：候选必须来自 OCR 实际识别文字，正则校验（单位后缀等）不过就留空。
8. **预览渲染按系统 DPI 缩放**（tk scaling 补偿），高分屏才清晰。

---

## 6. 版本历史（v0.1 → v1.5）

| 版本 | 日期 | 内容 |
|------|------|------|
| v0.1 | 2026-10-09 | MVP 全闭环：拆分+OCR 命名+无损导出，UI/打包/测试/文档齐备 |
| v1.1 | 2026-10-09 | 修复方向识别全错（cls 坑，见 §5-4）+ 侧放首页命名错误；真实样本实测 270° 建议正确 |
| v1.2 | 2026-10-09 | 方向弹窗"一键自动摆正"（可撤销）；预览重写（适应窗口/实际大小/Ctrl+滚轮/拖拽/高分屏） |
| v1.3 | 2026-10-09 | bug 复查轮 6 项修复：渲染缓存 512MB 字节预算、对话框 doc 绑定、方向建议跳过分隔页、方向分析无结果反馈、zoom/fit_mode 持久化、编辑时 Ctrl+Z 守卫 |
| v1.4 | 2026-10-10 | 用户报 3 个 bug：缩略图滚动不重绘；"填报单位：教务办公室"误识别成"自动化学院"（表格标注行规则+噪声过滤）；旋转摆正崩溃（撤销快照 dict/FieldCandidate 形态兼容） |
| v1.5 | 2026-10-10 | 新增公章（红章）文字提取 ocrseal.py：红色隔离+圆环极坐标展开弧形字；来源"印章"强制待确认 |
| v1.6 | 2026-10-10 | 体验优化：工具栏分 ①拆分②命名③导出 三段（未就绪置灰 + 状态栏"下一步"提示）；分组面板"整组识别方向"（按组过滤候选页，显式传 doc）；导出完成"打开输出目录"；空状态使用引导页；测试 100→101，补 Tk 创建抖动重试兜底 |

---

## 7. 关键坑（后续改动勿踩，全部真实踩过）

1. **PIL MaxFilter 在 cp314 崩溃** → 空白检测的形态学已改用纯 numpy 实现。
2. **pypdf rotate() 累加** → 直接写 `/Rotate`。
3. **RapidOCR cls 模型自动翻转侧放文字** → 方向对比必须用 NoCls 引擎（§5-4）。
4. **对话框打开时切换文档会把操作应用到错误文档** → apply_rotation / add_split_before
   等必须显式传 doc 参数，不要依赖"当前活动文档"。
5. **页面尺寸一致也会出现侧放页面**（横版表格嵌在纵向页里，几何看不出）→ 方向分析不能只按页面尺寸判断。
6. **空白检测信号要在形态学清洗后的 mask 上算**，否则边缘黑线/噪声会误判。
7. **缩略图条滚动必须重绘**：滚动条/滚轮回调里要调 redraw，否则滚入视口的页面是空白（v1.4 bug）。
8. **末页落款几何门槛要防表格单元格噪声**：无日期锚定时只认右下角（cy≥0.8h 且 cx/w≥0.5）；
   "填报单位：X" 这类含冒号的标注行要用 LABELED_UNIT_RE 单独匹配（整行 UNIT_PATTERN 匹配不到）。
9. **Group 的 title/unit_candidates 有两种形态**：OCR 命名后是 dict，其余时刻是 FieldCandidate。
   快照（model.Group.snapshot 的 _norm）与 UI 消费（GroupPanel 按 `c.get("text")` 读 dict）
   都必须兼容两种形态；restore 统一存 dict。新增代码处理候选时先想清楚形态。
10. **印章识别只在红色清晰时可靠**；弧形章字必须展开成直条再 OCR（常规引擎读不了弧形文字），
    展开时 θ 从 π 起绕一圈、上弧字正立；正/反两方向都要试（下弧字是倒置的）。
11. **诊断脚本的中文输出**要 `PYTHONIOENCODING=utf-8`，否则 Windows 控制台 GBK 会乱码/报错。
12. **Windows 控制台 + Git Bash 打包**：build.bat 含中文注释，从 Git Bash 经 cmd 调用会因
    编码错乱而失败；直接运行 `.venv\Scripts\python -m PyInstaller scansplit.spec --noconfirm`
    即可。robocopy 部署要 `MSYS_NO_PATHCONV=1`，/MIR 在 Git Bash 里要用 /E /PURGE。
13. **cp314 快速创建/销毁多个 Tk 根偶发 `tcl_findLibrary` 抖动**（v1.6 后在 UI 冒烟测试中
    频繁出现）：tests/test_ui_smoke.py 统一走 `_make_root()` 创建并重试兜底；新 UI 测试
    不要直接 `tk.Tk()`。

---

## 8. 测试体系与调试方法

- **全部测试用合成数据**，绝不读用户文件：PDF 级用 tests/samplegen.py 生成（含空白页、
  噪声、印章页），图像级用 tests/ocrimg.py（公文/公章/模糊/倾斜/表格）。
- OCR 相关测试有双重 skipif（无 OCR 组件 / 无中文字体），在其他机器上跑会自动跳过，
  不影响 CI 判定。
- **UI 无 mainloop 冒烟测试**（tests/test_ui_smoke.py）：withdraw 的 Tk root 直接驱动
  App 方法，monkeypatch 掉 messagebox 与 ProgressDialog。
- **对真实文件的诊断**模式（不要写进测试）：withdrawn Tk root + App 全流程 + FakeProgress +
  monkeypatch messagebox，参考此前临时脚本（如诊断 OCR 单位、印章、旋转摆正）。
- **新功能验收清单**：改完 → 定向测试 → 全量 pytest → 用真实测试样本跑一遍诊断 →
  更新 TEST_REPORT.md → 重新打包 + --selftest → 部署 D:\ScanSplitPDF 并哈希校验。

---

## 9. 构建与部署（精确命令）

```bash
cd /d/vibe-coding/scansplit-pdf
# 打包（Git Bash 下不要经 cmd 调 build.bat）
rm -rf build dist/ScanSplitPDF
.venv/Scripts/python -m PyInstaller scansplit.spec --noconfirm
# 自检
PYTHONIOENCODING=utf-8 dist/ScanSplitPDF/ScanSplitPDF.exe --selftest   # exit=0 且 SELFTEST OK
# 部署（先杀运行中的旧版）
tasklist //FI "IMAGENAME eq ScanSplitPDF.exe" | grep -i scansplit && taskkill //IM ScanSplitPDF.exe //F
MSYS_NO_PATHCONV=1 robocopy "D:\vibe-coding\scansplit-pdf\dist\ScanSplitPDF" "D:\ScanSplitPDF" /E /PURGE /NFL /NDL /NJH /NP /R:1 /W:1
# 校验部署（EXE SHA-256 与 dist 一致）
```

robocopy 退出码 0/1 都算成功（0=无变化，1=已复制）。打包产物约 250MB，含 OCR 模型。

---

## 10. 已知限制与可优化方向（接手后的候选任务）

现状限制（README/TEST_REPORT 有完整版）：

1. 加密 PDF 仅支持空密码；数字签名导出后失效（导入时警告）；
2. 无空白页分隔的连续扫描只能靠"复核拆分"人工补拆（OCR 标题特征只作第二信号）；
3. 长任务期间窗口短暂无响应（进度条与取消可用）；
4. 打包 onedir 约 250MB，不支持单文件；
5. 印章识别依赖红色清晰度；褪色/模糊印章会失败（标待确认）；
6. 真实文件拖放基于 Windows 消息子类化，个别环境可能无效（有"打开"按钮兜底）。

用户视角的优化方向（按用户反馈推演，未确认）：

- 印章：褪色红章的鲁棒性（红色阈值自适应）、蓝章/黑章（目前只处理红色）；
- 拆分：无空白页场景的智能拆分（目前靠人工）；
- 预览：长文档翻页性能、渲染缓存策略；
- OCR：识别速度（批量化）、低分辨率扫描件增强；
- UI：旋转多页交互、分组面板的批量编辑。

---

## 11. 与用户协作的注意事项（接手 Agent 必读）

- 用户是中文使用者，**沟通用中文，回复要简洁**；喜欢用选项方式做决策（给出推荐项）。
- 用户是"氛围编程"风格：迭代快、反馈以真实样本实测为主，理论分析为辅。
- **每个改动版本必须重新打包并替换 `D:\ScanSplitPDF`**——用户只验收打包版。
- 用户对"效果"的评价来自他手头的真实扫描文件（见 §1 测试样本路径），
  合成测试通过 ≠ 用户满意，真实样本实测是验收必需步骤。
- 尊重前述硬约束：任何"为了方便"而触碰源文件、重编码输出、联网调 API 的提议都会
  直接被否，不要提。
