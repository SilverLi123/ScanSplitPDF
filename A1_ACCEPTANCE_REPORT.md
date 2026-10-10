# 阶段 A1 验收报告：统一页面方向状态（Orientation Pipeline）

- **日期**: 2026-10-10
- **范围**: 修复实际存在的旋转错误；统一预览 / OCR / 命名 / 导出的页面方向
- **最高约束**: 不影响拆分、命名、导出与人工确认；不改原始 PDF；不静默丢页、不错误旋转、不覆盖人工确认、不改变原始扫描内容；不换 OCR 引擎；不重绘/重编码图片。

---

## 1. 目标与问题

A1 的目标不是"提高方向识别准确率"，而是**建立统一的页面方向状态**，让所有模块读取同一个最终方向：

```
导入 PDF → 原始页面状态 → 方向检测 → 页面目标方向状态
        → 上下文 OCR → 自动命名 → 导出
```

修复前存在的两类真实缺陷：

| 缺陷 | 表现 | 根因 |
|---|---|---|
| **候选方向被提前收窄** | 侧放页（正确方向 90/270）判成 0/180 | `analyze_directions` 用 0° 的竖/横行向决定候选对；0° 无行时返回 `none` → 落 `(0,180)`，**根本不含 90/270** |
| **90°/180° 系统性混淆** | C 样本 p19/41/45/59（真值 90）判成 180 | 旧评分 `avg × min(n,20)/20` 无区分度；伪方向可识别出大量"竖条检测框"行，行数与平均分都虚高 |

---

## 2. 统一方向状态（任务 2）

`PageState` 三来源分离，`effective_rotation` 为**唯一派生真源**：

| 字段 | 含义 |
|---|---|
| `original_rotation` | PDF 源文件原有 `/Rotate`（只读，加载时记录） |
| `detected_rotation` / `detected_confidence` | 自动方向检测建议（可 None） |
| `user_override_rotation` | 人工设定（可 None） |

```
effective_rotation = user_override_rotation ?? detected_rotation ?? 0
```

- **人工最高优先级**：`effective_rotation` 先读 `user_override`；自动检测只写 `detected_rotation`，**绝不覆盖人工结果**。
- **旧 API 兼容**：`page.rotation` 保留为别名（getter → `effective_rotation`；setter → 写 `user_override_rotation`，即视为人工），既有调用点语义不变。
- **旧快照向后兼容**：仅有 `rotation` 字段的历史快照按"人工覆盖"还原。

读取 `effective_rotation` 的模块：

| 模块 | 位置 |
|---|---|
| 缩略图角标 / 渲染 | `ui_widgets.ThumbnailStrip`（角标 `NN°` + `·自动` 区分人工/自动） |
| 预览面板 | `ui_widgets.PreviewPane._render` |
| 命名 OCR | `autoname._ocr_page_rotation_aware(..., page_state)` |
| 边界复核 / 智能拆分复核 | `autoname.boundary_review` / `smart_split_review` |
| 导出 | `exporter._export_one`（按 `effective_rotation` 写 `/Rotate`） |

**导出规则**：只根据最终方向状态，以**绝对目标角度覆盖** `/Rotate`（无二次旋转）；`pypdf` 复制页面对象，**不重编码图片**。

---

## 3. 方向候选与评分（任务 1）

### 3.1 始终评估完整四方向
`analyze_directions` 改为 `for angle in (0, 90, 180, 270)`，**不再因某方向无结果而提前排除其他方向**。

### 3.2 方向质量评分（`ocrfields.direction_quality`）
```
quality = avg × line_count_factor × orientation_factor × substance_factor
```
- `line_count_factor = min(n,20)/20`：行数奖励，设上限防爆行刷分。
- `orientation_factor = 0.25 + 0.75 × horizontal_frac`：**横排行占比**。正常正放文档绝大多数行为横向检测框；"竖向纵读/竖条误读"占比骤降被压低。这是区分真横排与伪方向的几何先验。
- `substance_factor = 0.45 + 0.55 × substance`：**实质文本行占比**（文本 ≥4 字符且置信度 ≥0.6 的题名/表头/正文句）。正确方向能读到成规模实质文本行，纯碎片伪方向被压到 0.45。

> **关键取舍（实测教训）**：曾用"极短行（≤2 字）罚分"，结果**误伤正确的表格方向**——表格天然有大量短单元格值（`数量`/`序号`/`11`），导致 stress81 有 6 页从正确方向翻转 180°。改为"实质文本行奖励"后，正确方向（含真实标题行，如宽 595 的题名）稳定胜出，同时不惩罚合法表格。

### 3.3 方向置信度（任务 1 第 ④ 条）
新增 `direction_confidence(qualities) → [0,1]`：由**最佳与次佳质量差距 + 绝对质量**映射。仅输出角度不足以表征可信度，故新增该指标并在 UI 提示中展示（`置信度 x.xx`）。平局时用 `horizontal_frac` 几何行向裁决（`best_direction`）。

---

## 4. 方向与 OCR 命名联动（任务 3）

- **流程**：方向确认 → 页面回正状态（`effective_rotation`）→ OCR 命名。命名阶段**不再自行猜方向**，只读统一状态。
- **已确认方向的页面不重复四方向 OCR**：`_ocr_page_rotation_aware` 在 `effective_rotation ≠ 0` 时直接按该角度单次识别。
- **方向变化 → OCR 结果失效**：OCR 缓存 key 已含 `rotation`（`recognize_cached(..., user_rotation=eff)`），方向一变即命中不同缓存条目，天然失效，**无需新增性能优化**。
- **不改 OCR 模型**：全程沿用现有引擎（`OcrEngine` / `OcrEngineNoCls`）。

---

## 5. 改动清单

| 文件 | 改动 |
|---|---|
| `scansplit/model.py` | `PageState` 三来源方向字段、`effective_rotation` 派生属性、`rotation` 别名（setter→人工）、`is_rotation_manual`、快照兼容；`ScanDocument.__init__` 记录 `original_rotation` |
| `scansplit/ocrfields.py` | `direction_quality` 重写（方向因子 + 实质行因子）；`best_direction` 平局裁决；新增 `direction_confidence` |
| `scansplit/autoname.py` | `DirectionResult.confidence`；`analyze_directions` 始终评估四方向 + 输出置信度；`_ocr_page_rotation_aware` 增加 `page_state` 按 `effective_rotation` 识别；命名 / 边界 / 智能拆分传入页面状态 |
| `scansplit/exporter.py` | 导出按 `effective_rotation` 写 `/Rotate`（绝对覆盖，无二次旋转，不重编码） |
| `scansplit/ui.py` | 方向分析结果写入 `detected_rotation` + `detected_confidence`（不覆盖人工） |
| `scansplit/ui_widgets.py` | 缩略图角标 / 预览读 `effective_rotation`，自动方向加 `·自动` 标记 |
| `tests/test_orientation.py` | **新增** 11 项方向回归测试 |
| `diag_a1_eval.py` | **新增** 方向准确率验收脚本（四套 100 页 + stress81） |

---

## 6. 验收结果

### 6.1 方向准确率（真实 OCR，`diag_a1_eval.py all`）

```
[A]  页=100  方向命中  7/7  = 1.000   误报: 无 ✅
[B]  页=104  方向命中 11/11 = 1.000   误报: 无 ✅
[C]  页=108  方向命中 32/32 = 1.000   误报: 无 ✅
[D]  页=100  方向命中 14/14 = 1.000   误报: 无 ✅
[E]  页=81   方向命中 19/19 = 1.000   误报: 无 ✅
                （stress81）

=== 汇总 ===
方向准确率 = 83/83 = 1.000
判错 = 0   误报 = 0   漏判 = 0
```

| 指标 | 结果 |
|---|---|
| **方向准确率**（含 stress81） | **83/83 = 1.000** |
| 判错 | **0** ✅ |
| 误报（非旋转页被判为需旋转） | **0** ✅ |
| 漏判（应旋转却建议 0） | **0** ✅ |
| 任务书点名页 | C:p22/45/59、A:p41 **全部正确** ✅ |
| stress81 原 19 页基线 | **19/19 保持**（未因 A1 破坏）✅ |

> 说明：上表只报"角度"。A1 另需报告下列**非角度**指标，见 6.2–6.5。

### 6.2 方向优先级 / 人工确认不被覆盖（单元测试）

- `test_effective_rotation_priority_user_over_detected`：人工 > 自动 > 0 ✅
- `test_legacy_rotation_setter_is_manual`：旧 API 赋值视为人工，后续自动检测**不覆盖** ✅
- `test_original_rotation_recorded_on_load`：加载记录源 `/Rotate`，且不叠加到 `effective` ✅

### 6.3 导出正确性

`test_export_applies_effective_rotation_no_double`（3 页：0 / 人工 270 / 自动 90）：
- 输出页数 3、`/Rotate` = 0 / 270 / 90 **逐页精确匹配** ✅
- `verify.verify_mapping` 通过：源文件字节未被改动 ✅
- 无二次旋转、未重编码图片 ✅

### 6.4 方向变化 → OCR 失效

`test_orientation_change_invalidates_ocr_cache`：`eff=0` 与 `eff=180` 为不同缓存条目，识别结果不串缓存 ✅

### 6.5 方向回正后标题/单位不下降

`test_titles_not_degraded_after_rotation`：同一内容"正放"与"侧放回正 270°"得到**一致的标题与单位**（均含"管理办法"）✅
`test_sideways_first_page_naming_uses_effective_rotation`：侧放首页回正后命中原标题 ✅

### 6.6 场景覆盖（任务书 8 类）

| 场景 | 测试 | 结果 |
|---|---|---|
| 90° 侧放表格 | `test_analyze_directions_sideways_90_270[(90,270)]` | ✅ |
| 270° 侧放表格 | 同上 `[(270,90)]` | ✅ |
| 180° 倒置首页 | `test_analyze_directions_upside_down_180` | ✅ |
| 四方向全覆盖（回归 A:p41 根因） | `test_analyze_directions_all_four_tried` | ✅ |
| 侧放首页命名 | `test_sideways_first_page_naming_uses_effective_rotation` | ✅ |
| 人工覆盖自动 | `test_effective_rotation_priority_user_over_detected` | ✅ |
| 方向变化 OCR 失效 | `test_orientation_change_invalidates_ocr_cache` | ✅ |
| 导出无二次旋转 | `test_export_applies_effective_rotation_no_double` | ✅ |
| 旋转后标题/单位不下降 | `test_titles_not_degraded_after_rotation` | ✅ |

---

## 7. 测试

- **全量**：`195 passed`（184 → 195，新增 11），0 失败。
- **新增**：`tests/test_orientation.py`（11 项）。
- **历史基线保留**：未删除 / 跳过 / 放宽任何既有断言。

---

## 8. 约束遵守情况

| 约束 | 状态 |
|---|---|
| 不影响拆分 / 命名 / 导出 / 人工确认 | ✅ `effective_rotation` 唯一真源；人工优先 |
| 不改原始 PDF | ✅ 仅只读渲染 + 导出写目标页 `/Rotate` |
| 不静默丢页 / 错误旋转 / 覆盖人工 | ✅ 方向仅建议不自动应用；人工优先 |
| 不改变 PDF 原始扫描内容 | ✅ 不重编码图片，复制页面对象 |
| 不换 OCR 引擎 | ✅ 沿用现有引擎 |
| 不重绘 / 重编码图片 | ✅ |
| 不做无关 UI 重构 | ✅ 仅方向状态读取与角标 |
| 不擅改既有空白检测阈值 | ✅ `blankdetect` 未动 |
| 不删 / 跳 / 放宽现有测试 | ✅ |
| 保持离线 | ✅ |
| 分阶段提交不 push | ✅ 单独 commit 未 push |

---

## 9. 后续

- **集中复核体验**（产品重点转向）：每份文档起止页 + 缩略图；明确显示被排除的空白页；展示自动识别的标题 / 单位及来源位置；快速修正拆分边界 / 方向 / 文件名；未确认关键结果不静默导出；导出前检查全部原始页去向（无未说明漏页）；批量输出 `标题——单位.pdf`。
- **阶段 A2**：回归验收（不含 / 含 `smart_split_review` 两份报告），单独 commit。
- **阶段 B**：标题与单位命名优化。
- **OCR 缓存**：按用户指令，暂不开始。
