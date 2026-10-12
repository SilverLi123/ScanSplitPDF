# ScanSplit PDF — 项目交接文档（给接手的 Agent）

## 当前维护入口：v1.14

当前版本1.14.0，分支 `codex/reliable-scansplit`，基线 `8b57298`（321项）。当前说明/验收：`work/v114-user-guide.md`、`work/v114-delivery-report.md`、`TEST_REPORT.md`。

- `pipeline.organize_document` 是 UI 自动分析入口：分析后仅命名可靠分组；单文档事务在取消/异常时完整回滚。既有 `analyze_document` 仍为仅分析接口。
- `automation.clearly_upright` 只认可保持当前0°且四方向证据完整：足够横排实质文字，反向弱、侧向没有竞争。少文字的旧综合分数不是唯一依据；非零旋转从不自动应用，人工方向优先。
- `pagination_read.refine` 对不可靠页码做2400px页边区域OCR，几何映射回整页后使用原严格阈值；整页与区域冲突、页眉页脚冲突不能选一个。缓存模式/原件签名/角度/宽度分离，空结果不缓存以免掩盖临时故障。
- `jointsplit.check_sequence` 只用邻页矛盾否决拆分，不推测未知页码。裸数字重启需前后连续序列；明确Page/第N页仍可独立判断。人工起点和确认排除的分隔页隔断序列。
- `automation.name_ready` 跳过方向/边界/过期分析与实际pending范围；部分组命名不标全局naming_attempted。使用现有 reporting/preserve_confirmed 命名规则，人工编辑优先。
- 清晰自动分析直接定位名称卡片；最后一个待确认交界解决后只继续一次，不在导航/跳过时运行识别。自动正放在预览标注“规则检查通过”，不冒称人工确认。
- `tools.acceptance_automation.py` 读取v1.13保存的同一批PDF与基线记录做真实OCR比较；三个清晰场景方向/拆分/名称全自动，最后名称批准明确模拟人工。低分中文与侧放/倒置必须维持待确认；历史11页样本仅只读诊断，没有业务准确率gold。
- OCR引擎/模型、命名/字段算法、旧检测/上下文、方向算法、导出与保真算法无变更。新策略和编排不改变原件字节。
- 独立运行目录 `D:/ScanSplitPDF-Reliable-1.14.0` 与同名ZIP，旧包保留；真实业务准确率、几百页体验、干净第二台Windows及项目保存/恢复仍未完整验收。

## 历史维护入口：v1.13

当前版本 1.13.0，分支 `codex/reliable-scansplit`，基线 `1eb3a1e`（v1.12.0，275 项通过）。最新操作与证据以 `work/v113-user-guide.md`、`work/v113-delivery-report.md` 和 `TEST_REPORT.md` 为准。

- 用户明确授权增强拆分：空白不是必要条件；无空白时可按可靠印刷页码判断，标题作为辅助。之前 v1.12 的 UI-only 范围仅约束历史任务。
- `pagenum.pagination_evidence` 是自动判断的严格入口，区分文本、数字、总页数和识别证据；旧提取接口继续兼容旧测试与历史建议。
- `jointsplit.decide` 比较相邻内容页，有无空白均适用。明确重启到 1 且无矛盾时拆分；连续则保留；跳号、缺失/低分、候选冲突、续表附件重启、总页数不完整等交人工确认。无空白的 1→1 需要已知前份共 1 页，不能猜单页文档。
- 间隔空白默认留在前份；不自动排除。OCR 引擎、空白检测、旧上下文规则、字段命名、方向与导出保真算法未改。
- `ScanDocument.boundary_evidence` 保存实际页码及原页号证据，撤销深拷贝且兼容旧快照。复核窗口/页面摘要只读显示。重分析清除过时自动拆分点，人工边界及人工名称仍优先。
- `tools.acceptance_pagination.py` 用真实离线 OCR 和图像型合成 PDF 验证新流程；方向/名称为显式模拟人工确认，边界从不人工纠正。必须分别报告初次自动状态、方向确认后的自动分组和弱页码阻断，不能把模拟人工计为自动结果。
- OCR 分数阈值不是校准正确概率；部分中文短页脚读出数字但分数低，会要求人工确认。当前未建立真实业务准确率承诺。
- 独立运行包 `D:/ScanSplitPDF-Reliable-1.13.0` 与同名 ZIP，旧包保留。关闭后恢复项目及另一台干净 Windows 验收尚未实现/完成。

## 历史维护入口：v1.12

下面 v1.5–v1.9 的交接记录保留为历史，涉及现状时优先阅读本节、项目 `AGENTS.md` 及 `work/v112-ui-plan.md`、`work/v112-user-guide.md`、`TEST_REPORT.md`。

- 开发分支 `codex/reliable-scansplit`，本轮基线 `bb8cca6`（v1.11.1，257 项回归通过）。
- 产品是本地扫描 PDF 自动整理工具，单位字段只认明确的“填报单位”；不以文头、落款或印章替代。
- 当前流程：导入 → 自动分析 → 检查拆分 → 确认名称 → 无损导出。`pipeline.py` 已统一拆分入口，`workflow.py` 提供只读下一步。
- `presentation.py` 是只读展示层：状态计数、问题定位、来源与页面摘要、导出反馈。不得在展示或导航中确认字段、应用方向或更改边界。
- `review_ui.py` 提供方向预览确认、待确认交界、全部交界和范围总览。跳过保留未处理状态；没有保存的规则证据不能编造。
- `ui_widgets.py` 中的检查中心与名称页签共用右侧栏，确认按钮固定在名称页签底部。字段来源按当前实际值匹配，人工文字不冒用首个 OCR 候选。
- `Group.title_edit_kind/unit_edit_kind` 仅记录展示来源（edited/confirmed/candidate）；空值表示旧记录未区分。快照和唯一对应迁移需保留它们，不能因此改变确认规则。
- 导出预检与实际保真/覆盖校验必须区分；取消时未执行的覆盖率校验不能展示“通过”。修改状态使旧检查显示失效，不在每次输入时重新 SHA-256 扫描文件。
- 本轮不修改 OCR 引擎、拆分/命名算法、导出/验证逻辑；`grouping.py` 仅复制两项展示元数据。原页复制、人工优先和强制完整性预检继续有效。
- 交付到独立版本目录，保留旧包，避免强制关闭用户仍有未导出的会话。只运行本次构建的包做自检，不能用旧 EXE 证明新源码。
- 真正的用户业务样本、跨机器 Windows 兼容性和关闭后恢复项目仍未完成验收；历史样本指标不代表当前填报单位模式准确率。

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
| 测试 | `.venv\Scripts\python -m pytest`（当前 133 passed，约 85s；无 OCR/无中文字体的机器上相关用例自动跳过） |
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
| `blankdetect.py` | 空白页检测 | 多信号（墨迹比/深色比/梯度比/包围盒）+ 三级判定（空白/疑似/有内容）；信号在形态学清洗后的 mask 上算；印章/浅签名 → 疑似（保守）；v1.7 起带稀疏内容页护栏（残留墨迹 ink_floor + 最长深色水平段 dark_run_frac） |
| `grouping.py` | 分隔页 → 文档组 | 纯函数；连续空白合并、首尾空白不产生空组、内部空白保留；v1.7 起 `compute_groups(separators, n, split_starts)` 支持拆分边界（新文档起点，不吞页）；v1.8 起 `refresh_groups` 做人工编辑迁移：唯一对应迁移标题/单位/确认状态，歧义（一对多）保留候选+标记待确认，说明写入 `doc.group_migrate_notes` |
| `pagenum.py` | 印刷页码提取（v1.7） | 页脚优先/页眉回退；整行模式匹配（纯数字/第X页/X/Y/—X—/Page X），防落款日期误判；reset_points 给页码重置候选。只与标题特征双信号一致才自动拆分 |
| `render.py` | 页面渲染 | PageRenderer，**必须在同一线程使用（Tk 主线程）**；LRU 缓存 key=(idx0, bucket, width, total_rot)；512MB 字节预算淘汰；`close()` 清缓存 |
| `ocr.py` | OCR 引擎封装 | OcrEngine（常规，含角度分类 cls）/ OcrEngineNoCls（use_angle_cls=False）；模型完整性校验；**cls 会把 90/180/270° 文字自动翻转识别**（方向分析的根源坑，见 §7） |
| `ocrfields.py` | 标题/单位字段提取 | 纯几何+格式启发式；标题多行拼接；单位分文头/落款/正文提及；LABELED_UNIT_RE 表格标注行；`merge_unit_candidates(first, last, seal)` |
| `ocrseal.py` | 公章（红章）文字提取 | 红色像素隔离 → 圆环检测 → 极坐标展开弧形字 → OCR；来源"印章"封顶 0.7 置信度 |
| `autoname.py` | 命名编排、方向分析、边界复核 | `_ocr_page_rotation_aware` 返回 (lines, w, h, img)；侧放页面自动按回正方向识别（**只用于识别，绝不自动改旋转**）；印章来源的 unit 强制 need_confirm；v1.7 起 `smart_split_review` 为"复核拆分/补拆"入口（页码+标题双重验证），旧 `boundary_review` 保留兼容 |
| `rotation.py` | 旋转目标角度管理 | 只写 `/Rotate`；orientation_suggestions 跳过空白分隔页 |
| `naming.py` | 文件名模板/非法字符/防覆盖/单位历史 | 空名回退逻辑 |
| `exporter.py` | 批量导出 | 预检（v1.8 严格模式默认开启：空标题/空单位/待确认字段为阻断项）、逐份保真校验、取消、失败不影响其余；v1.8 起导出后做**全局页面覆盖率校验**（每个有效原页恰好导出一次，漏页/重复页报 [严重]，用户取消则跳过） |
| `verify.py` | 保真校验 | v1.8 起双指纹：解码内容流哈希 + 原始编码流哈希 + 解码/原始图像哈希 + 页面映射 + SHA-256；**读取异常 fail-closed**（指纹 errors 非空即判失败，绝不静默放行）；区分"解码数据一致"与"原始编码流字节一致"（重编码会报不一致） |
| `model.py` | 数据模型 + 撤销快照 | Workspace.snapshot/restore 是撤销最小单元；**Group 的候选字段有两种形态**（见 §7 坑 9）；v1.8 起 `need_confirm` 为派生属性（= 标题或单位任一未确认），字段级状态 `title_confirmed`/`unit_confirmed`（默认 True，OCR 命名按置信度回落；setter 兼容旧整组赋值与旧快照） |
| `settings.py` | 设置持久化 | zoom/fit_mode 也要保存恢复 |
| `ui.py` | 主窗口编排 | 撤销/重做、进度对话框、对话框的 doc 绑定（防切换文档误操作，见 §7 坑 4）；v1.6 起工具栏按钮经 `self.tool_btns` 管理、`_update_step_state` 按工作流阶段置灰/启用，`direction_analyze_group` 支持组级方向分析 |
| `ui_widgets.py` | 缩略图条/分组面板/预览 | GroupPanel 按 **dict 风格**消费候选（`c.get("text")`）；缩略图滚动必须 redraw |

`tests/`：14 个测试文件，133 用例。合成数据生成：`tests/samplegen.py`（PDF 级）、
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

## 6. 版本历史（v0.1 → v1.8）

| 版本 | 日期 | 内容 |
|------|------|------|
| v0.1 | 2026-10-09 | MVP 全闭环：拆分+OCR 命名+无损导出，UI/打包/测试/文档齐备 |
| v1.1 | 2026-10-09 | 修复方向识别全错（cls 坑，见 §5-4）+ 侧放首页命名错误；真实样本实测 270° 建议正确 |
| v1.2 | 2026-10-09 | 方向弹窗"一键自动摆正"（可撤销）；预览重写（适应窗口/实际大小/Ctrl+滚轮/拖拽/高分屏） |
| v1.3 | 2026-10-09 | bug 复查轮 6 项修复：渲染缓存 512MB 字节预算、对话框 doc 绑定、方向建议跳过分隔页、方向分析无结果反馈、zoom/fit_mode 持久化、编辑时 Ctrl+Z 守卫 |
| v1.4 | 2026-10-10 | 用户报 3 个 bug：缩略图滚动不重绘；"填报单位：教务办公室"误识别成"自动化学院"（表格标注行规则+噪声过滤）；旋转摆正崩溃（撤销快照 dict/FieldCandidate 形态兼容） |
| v1.5 | 2026-10-10 | 新增公章（红章）文字提取 ocrseal.py：红色隔离+圆环极坐标展开弧形字；来源"印章"强制待确认 |
| v1.6 | 2026-10-10 | 体验优化：工具栏分 ①拆分②命名③导出 三段（未就绪置灰 + 状态栏"下一步"提示）；分组面板"整组识别方向"（按组过滤候选页，显式传 doc）；导出完成"打开输出目录"；空状态使用引导页；测试 100→101，补 Tk 创建抖动重试兜底 |
| v1.7 | 2026-10-10 | 智能补拆（无空白页场景自动化）：新增 pagenum.py（印刷页码提取+页码重置信号）；autoname.smart_split_review 双重验证（页码重置+标题特征双信号一致才自动补拆，单信号仅提示）；**拆分边界 split_before 概念**（记录在起点页自身，内容页不被吞）；修复稀疏内容页误判空白（一行文字页墨迹比被腐蚀到 0.01% 低于阈值 → 新增残留墨迹+最长深色水平段双护栏，降级"疑似"）；修复复核拆分在无空白页场景丢前一页的缺陷；测试 101→111 |
| v1.8 | 2026-10-10 | 可靠性交付加固：①**字段级确认状态**（Group.title_confirmed/unit_confirmed，need_confirm 变派生属性）——修复"编辑标题就清除整组待确认"缺陷，印章单位仍强制待确认；②refresh_groups 人工编辑迁移（唯一对应迁移、歧义保留候选+待确认，绝不静默丢失，说明经 UI 日志转述）；③verify 双指纹（解码+原始编码流）与读取异常 fail-closed；④exporter 全局页面覆盖率校验（每个有效原页恰好导出一次，防内部空白页误删）+ **严格导出模式**（默认开启，空标题/空单位/待确认阻断，用户显式选择非严格模式仅限测试文件）；测试 116→133 |

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
14. **separator 与 split_before 语义完全不同，不可混用**：`separator`（空白分隔页）会把
    该页**排除出所有分组**（导出时丢弃该页）——只适用于真正的空白页；内容页之间的拆分
    必须用 `split_before`（新文档起点，页保留在组内，model.PageState +
    grouping.compute_groups 的 split_starts 参数）。v1.7 前的 add_split_before 把前一页
    标成分隔页，在无空白页场景会导致丢页，已修复。
15. **稀疏内容页会被形态学腐蚀成"准空白"**：整页只有一行文字时，清洗后墨迹比可低到
    0.01%（远低于 0.4% 阈值），单靠墨迹比必然误判。blankdetect.classify 已加双护栏
    （`ink_floor_blank_pct` 清洗后残留 + `dark_run_frac` 最长深色水平段占页宽比，
    文字笔画 ~2.6% vs 噪点 0.3%）→ 此类页面降级"疑似"。调整这两个参数前先跑
    test_blankdetect 全部用例。
16. **need_confirm 是派生属性，不能当普通字段重新赋值后另行保存**（v1.8）：它 = 标题或
    单位任一未确认。要改确认状态请操作 `title_confirmed`/`unit_confirmed`；整组赋值
    `g.need_confirm = x` 走 setter（映射到两个字段），仅为旧代码兼容保留。快照兼容：
    旧快照只有 need_confirm 键时按整组语义还原。
17. **refresh_groups 的迁移只在"唯一对应"时安全**：新组↔旧组必须一对一（按页重叠判定）
    才迁移标题/单位；一对多（拆组/并组）属歧义——不猜标题，合并候选并强制待确认。
    迁移/歧义说明写入 `doc.group_migrate_notes`，UI 用 `_log_migrate_notes` 转述。
    verify 的指纹含 errors 列表：**任何读取异常都 fail-closed 判失败**，不要把
    errors 静默吞掉"优化"掉。

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
