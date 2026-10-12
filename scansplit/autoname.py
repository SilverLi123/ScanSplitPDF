"""OCR 自动命名编排：标题/单位提取、横向页方向分析、拆分边界双重验证。

- 空白页检测生成分隔候选；其业务属性（分隔/内部空白/待确认）由 splitctx
  结合上下文判定（见 grouping/splitctx），只有分隔页才排除，其余默认保留。
- 无空白页分隔的连续件由 smart_split_review 补拆：印刷页码重置 + 标题特征
  双信号一致才自动补拆（可撤销），单信号仅提示人工确认，绝不凭单一信号改动分组。
- OCR 结果全部来自实际识别文字，识别失败不编造，字段标记"待确认"。
- 方向分析（analyze_directions）仅给出建议并写入 detected_rotation，
  绝不自动旋转；是否采用由用户在方向对话框中确认。
- OCR 只读取渲染图像，绝不写回 PDF。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import naming, ocr_cache, ocrfields, pagenum, unit_region
from .model import ScanDocument

CONFIRM_THRESHOLD = 0.55  # 低于此置信度标记"待确认"

# 允许自动确认的单位来源（须有可靠结构证据）：
# 落款=日期锚定/右下角落款区；文头=红头/页眉或"填报单位："标注；文本层=PDF 内嵌文字。
# "正文"仅是正文提及（置信度 ×0.6 的弱信号，如句子截断片段），无结构证据，
# v1.9 起不得仅凭置信度过阈值就自动确认（P1 修复：噪声"为进一步完善各部"0.562 曾过线）。
# "印章"来源本就强制待确认（可靠性有限），不在此列。
UNIT_AUTO_CONFIRM_SOURCES = {"落款", "文头", "文本层"}


def _unit_auto_confirm(res: NameResult) -> bool:
    """单位字段是否允许自动确认。

    规则（v1.9 P1 修复）：
    1. 候选来源必须具有可靠结构证据（UNIT_AUTO_CONFIRM_SOURCES）；
    2. 置信度不低于 CONFIRM_THRESHOLD；
    3. 无歧义：若存在另一可靠来源候选、文本不同、且置信度接近（差 < 0.05），
       交人工裁决。
    标题字段的确认逻辑不受本函数影响。
    """
    best_u = res.best_unit
    if not best_u or not best_u.text.strip():
        return False
    if best_u.source not in UNIT_AUTO_CONFIRM_SOURCES:
        return False
    if best_u.confidence < CONFIRM_THRESHOLD:
        return False
    for c in res.unit_candidates[1:]:
        if (
            c.source in UNIT_AUTO_CONFIRM_SOURCES
            and c.text.strip() != best_u.text.strip()
            and best_u.confidence - c.confidence < 0.05
        ):
            return False
    return True


@dataclass
class NameResult:
    group_index: int
    title_candidates: list = field(default_factory=list)
    unit_candidates: list = field(default_factory=list)
    used_ocr: bool = False
    error: str = ""

    @property
    def best_title(self):
        return self.title_candidates[0] if self.title_candidates else None

    @property
    def best_unit(self):
        return self.unit_candidates[0] if self.unit_candidates else None


def reporting_unit_auto_confirm(res: NameResult) -> bool:
    candidates = [c for c in res.unit_candidates if c.source == "填报单位"]
    return bool(res.best_unit and res.best_unit.source == "填报单位"
                and candidates and candidates[0].confidence >= .85
                and len({c.text.strip() for c in candidates}) == 1)


@dataclass
class DirectionResult:
    page: int
    qualities: dict  # angle -> {n_lines, avg_score, quality, ...}
    suggested_angle: int | None = None
    suggestion_note: str = ""
    confidence: float = 0.0  # 方向判定置信度 [0,1]（v1.9 A1）


@dataclass
class BoundaryStatus:
    separator_page: int | None  # None 表示"建议新增拆分点"
    next_page: int | None
    title_score: float
    status: str  # ok | warn | suggest_split | auto_split
    note: str
    page_num: int | None = None  # 该页识别到的印刷页码（无则 None）


def _page_text_lines(reader, page_idx0: int) -> list:
    """文本层标题候选（有文字层的 PDF 用，比 OCR 更可靠）。"""
    titles = naming.extract_title_candidates(reader, page_idx0)
    return [ocrfields.FieldCandidate(t, 0.9, "文本层", reason="从 PDF 文本层提取")
            for t in titles]


def _has_text_layer(reader, page_idx0: int) -> bool:
    try:
        text = reader.pages[page_idx0].extract_text() or ""
        return len(text.strip()) > 10
    except Exception:
        return False


def _ocr_page_rotation_aware(renderer, idx0: int, engine, width_px: int, page_state=None):
    """识别页面文字，返回 (lines, w, h, img)。

    v1.9 A1 统一方向：命名阶段**不再自行猜测方向**，而是读取页面的统一方向状态
    `effective_rotation`（人工覆盖 ?? 自动检测 ?? 0）——保证预览/OCR/命名/导出
    四者方向一致，且不会出现"OCR 命名后又发现方向错误需重新 OCR"的反复。

    page_state 为 None 时（旧调用 / 无文档上下文）退回兼容行为：仅当 0° 结果为
    竖排（内容侧放）时，用禁用 cls 的引擎在 90/270 中选质量更高者。此时同样
    应用新的方向质量评分（含横向占比因子），并把 4 方向纳入候选。
    """
    eff = int(getattr(page_state, "effective_rotation", 0) or 0) % 360
    if page_state is not None or eff:
        img = renderer.render(idx0, width_px=width_px, user_rotation=eff,
                              bucket="ocr-eff")
        lines = ocr_cache.recognize_cached(engine, "cls", renderer, idx0, width_px, eff)
        w, h = img.size
        return lines, w, h, img

    img = renderer.render(idx0, width_px=width_px, bucket="ocr")
    lines = ocr_cache.recognize_cached(engine, "cls", renderer, idx0, width_px)
    w, h = img.size
    kind, _frac = ocrfields.line_orientation(lines)
    if kind != "vertical":
        return lines, w, h, img
    from .ocr import OcrEngineNoCls

    raw = OcrEngineNoCls.get()
    if not raw.available:
        return lines, w, h, img  # 无引擎可判断，保留 0° 结果（待确认兜底）
    # 兼容路径也把四个方向都纳入候选（避免侧放页被 90/270 之外的错误方向锁死）
    best_lines, best_wh, best_img, best_q = None, None, img, -1.0
    for angle in (90, 270):
        img2 = renderer.render(idx0, width_px=width_px, user_rotation=angle, bucket="ocr-rot")
        ls = ocr_cache.recognize_cached(raw, "nocls", renderer, idx0, width_px, angle)
        q = ocrfields.direction_quality({angle: ls})[angle]["quality"]
        if q > best_q:
            best_lines, best_wh, best_img, best_q = ls, img2.size, img2, q
    wh = best_wh or (w, h)
    return best_lines or [], wh[0], wh[1], best_img


def auto_name_groups(
    doc: ScanDocument,
    renderer,
    engine=None,
    progress_cb=None,
    cancel_cb=None,
    only_indices: set[int] | None = None,
    preserve_confirmed: bool = False,
    unit_mode: str = "legacy",
) -> list[NameResult]:
    """为每份分组自动提取标题与发文单位并填入组。

    engine 为 None 时仅走文本层路径（无 OCR 环境）。

    only_indices：只处理这些组下标（None = 全部）。用于"拆分边界调整后
    重新识别受影响分组"，避免对未受影响的组重复 OCR。
    preserve_confirmed：True 时**不覆盖**已人工确认（*_confirmed=True 且已有值）
    的字段——重新识别只填补空白/待确认字段，绝不抹掉用户已确认的结果。
    """
    results: list[NameResult] = []
    total = len(doc.groups)
    for gi, g in enumerate(doc.groups):
        if cancel_cb and cancel_cb():
            break
        if only_indices is not None and gi not in only_indices:
            continue
        # 人工已确认的字段在 preserve_confirmed 下受保护（重识别只填空白字段）
        keep_title = g.title_manual or (preserve_confirmed and g.title_confirmed and g.title.strip())
        keep_unit = g.unit_manual or (preserve_confirmed and g.unit_confirmed and g.unit.strip())
        res = NameResult(group_index=gi)
        first0, last0 = g.start - 1, g.end - 1
        if unit_mode == "reporting":
            nonblank = [number-1 for number in g.pages if number not in doc.excluded_pages
                        and (not doc.pages[number-1].blank or doc.pages[number-1].blank.verdict != "blank")]
            if not nonblank:
                continue  # No retained content page to name.
            first0, last0 = nonblank[0], nonblank[-1]

        # 1) 文本层（可选增强）
        if unit_mode == "reporting" and any(p.has_rotation_proposal for p in doc.pages[g.start-1:g.end]):
            res.error = "方向待确认，暂不命名"
        elif _has_text_layer(doc.reader, first0) and (unit_mode == "legacy" or engine is None):
            res.title_candidates = _page_text_lines(doc.reader, first0)
            last_titles = naming.extract_unit_candidates(doc.reader, last0) if unit_mode == "legacy" else []
            for t in last_titles:
                res.unit_candidates.append(
                    ocrfields.FieldCandidate(t, 0.85, "文本层", reason="末页文本层提取")
                )
            res.used_ocr = False
        elif engine is not None and engine.available:
            res.used_ocr = True
            try:
                lines, w, h, img1 = _ocr_page_rotation_aware(
                    renderer, first0, engine, 1200, doc.pages[first0])
                res.title_candidates = ocrfields.extract_title(lines, w, h)
                if unit_mode == "reporting":
                    structure = ocrfields.first_page_structure(lines, w, h)
                    expected = structure.get("title_text", "")
                    strong = [c for c in res.title_candidates
                              if ocrfields._title_pattern_score(c.text) >= 1
                              and expected and (expected in c.text or c.text in expected)]
                    if strong:
                        res.title_candidates = strong
                    for c in res.title_candidates:
                        matching = [l for l in lines if l.text.strip() and l.text.strip() in c.text]
                        if matching:
                            c.bbox = (min(l.x0 for l in matching), min(l.y0 for l in matching),
                                      max(l.x1 for l in matching), max(l.y1 for l in matching))
                            c.image_size = (w, h)
                first_units = ocrfields.extract_unit_first_page(lines, w, h)
                if last0 != first0:
                    last_lines, lw, lh, img_last = _ocr_page_rotation_aware(
                        renderer, last0, engine, 1200, doc.pages[last0]
                    )
                    last_units = ocrfields.extract_unit_last_page(last_lines, lw, lh)
                else:
                    # 单页文档：同一页既是首页也是末页（落款就在首页上）
                    last_units = ocrfields.extract_unit_last_page(lines, w, h)
                    img_last = img1
                seal_units: list = []
                try:
                    from . import ocrseal

                    seal_units = ocrseal.extract_seal_units(img1, engine) if unit_mode == "legacy" else []
                    if last0 != first0 and unit_mode == "legacy":
                        seal_units += ocrseal.extract_seal_units(img_last, engine)
                except Exception:
                    seal_units = []  # 印章识别失败不影响主流程
                res.unit_candidates = ocrfields.merge_unit_candidates(
                    first_units, last_units, seal_units
                )
                if unit_mode == "reporting":
                    # Explicit reporting labels remain primary. Last-page signatures
                    # are separate, unconfirmed suggestions authorised in v1.16.
                    res.unit_candidates = []
                    page_data = [(first0, lines, w, h)]
                    for idx0 in nonblank[1:]:
                        if cancel_cb and cancel_cb():
                            break
                        if doc.pages[idx0].has_rotation_proposal:
                            continue
                        if idx0 == last0:
                            page_data.append((idx0, last_lines, lw, lh))
                        else:
                            ls, pw, ph, _ = _ocr_page_rotation_aware(renderer, idx0, engine, 1200, doc.pages[idx0])
                            page_data.append((idx0, ls, pw, ph))
                    for idx0, ls, pw, ph in page_data:
                        for c in ocrfields.extract_reporting_unit(ls, pw, ph):
                            c.page = idx0 + 1
                            res.unit_candidates.append(c)
                    res.unit_candidates.sort(key=lambda c: c.confidence, reverse=True)
                    if not (cancel_cb and cancel_cb()):
                        regional = unit_region.scan(renderer, last0, engine, doc.pages[last0],
                                                    cancel_cb or (lambda: False))
                        res.unit_candidates = unit_region.merge(res.unit_candidates, regional)
                for c in res.title_candidates:
                    c.page = first0 + 1
            except Exception as e:
                res.error = str(e)

        # 2) 填入组字段
        if cancel_cb and cancel_cb():
            break  # A cancelled region/page scan must not publish partial names.
        best_t = res.best_title
        best_u = res.best_unit
        if keep_title:
            pass  # 人工已确认的标题保持不变，仅刷新候选
        elif best_t and best_t.confidence >= CONFIRM_THRESHOLD:
            g.title = best_t.text
            g.title_conf = best_t.confidence
        else:
            g.title = best_t.text if best_t else ""
            g.title_conf = best_t.confidence if best_t else None
        if keep_unit:
            pass  # 人工已确认的单位保持不变，仅刷新候选
        elif best_u and best_u.confidence >= CONFIRM_THRESHOLD:
            g.unit = best_u.text
            g.unit_conf = best_u.confidence
        else:
            g.unit = best_u.text if best_u else ""
            g.unit_conf = best_u.confidence if best_u else None
        g.title_candidates = [c.to_dict() for c in res.title_candidates]
        g.unit_candidates = [c.to_dict() for c in res.unit_candidates]
        # 字段级确认状态（v1.8）：标题与单位分别按置信度判定，
        # 人工编辑单字段只影响该字段的确认状态。
        # v1.9 P1 修复：单位自动确认增加结构证据来源校验 + 歧义检查
        # （_unit_auto_confirm），正文噪声不再凭置信度过线直接落盘。
        # 人工已确认状态不会被此处清除：本函数只在 OCR 命名时写入，
        # 已有人工编辑的组由 UI 层守卫（本流程启动前提示）。
        if not keep_title:
            g.title_confirmed = bool(
                res.title_candidates
                and best_t
                and best_t.text.strip()
                and best_t.confidence >= CONFIRM_THRESHOLD
            )
        if not keep_unit:
            g.unit_confirmed = (reporting_unit_auto_confirm(res) and not res.error if unit_mode == "reporting"
                                else _unit_auto_confirm(res))
        if unit_mode == "reporting":
            if not keep_title:
                g.title_confirmed = bool(g.boundary_confirmed and best_t and best_t.confidence >= .85
                    and ocrfields._title_pattern_score(best_t.text) >= 1
                    and len({c.text for c in res.title_candidates}) == 1 and not res.error)
            if not keep_unit and not g.boundary_confirmed:
                g.unit_confirmed = False
        results.append(res)
        if progress_cb:
            progress_cb(gi + 1, total)
    # v1.9.1 改进6：命名后同步维护"受影响组"清单——本次处理过且已不再
    # 需要人工确认的组，从 affected_group_indices 中移除，避免 UI 反复提示
    # "有受影响组需重新识别"（消除陈旧标记导致的空转与误判）。
    if getattr(doc, "affected_group_indices", None):
        handled = only_indices if only_indices is not None else set(range(len(doc.groups)))
        still = [
            gi for gi in doc.affected_group_indices
            if 0 <= gi < len(doc.groups)
            and not (gi in handled and not doc.groups[gi].need_confirm)
        ]
        doc.affected_group_indices = still
    return results


def analyze_directions(
    doc: ScanDocument,
    renderer,
    pages_1based: list[int],
    progress_cb=None,
    cancel_cb=None,
    fast: bool = False,
) -> list[DirectionResult]:
    """对指定页面做 0/90/180/270 四方向 OCR 质量对比，辅助判断正确方向。

    v1.9 A1 修复（仅修实际存在的旋转错误）：
    1) **始终**评估完整四方向——旧的"0° 无行就锁 (0,180)"会把侧放页（正确方向
       是 90/270）提前排除在本就不含正解的候选对里，必然判错（A:p41 类缺陷）。
    2) 方向质量加入碎片/单一区域/行数爆炸惩罚 + 平局用几何行向先验
       （见 ocrfields.direction_quality / best_direction），缓解 90°/180° 混淆。
    3) 输出 confidence（最佳与次佳差距 + 绝对质量），不再只有角度。

    两类信号（缺一不可）：
    1) 检测框几何（横/竖行占比）：纯几何，可靠区分"内容正放/侧放"；
    2) 禁用 cls 的 OCR 质量：cls 模型会把倒置/侧放文字自动翻转识别，
       常规引擎对四方向都能读通（质量无区分度）；禁用 cls 后倒置文字
       质量骤降，即可在四方向内选出正确方向。

    仅给出建议，绝不自动旋转——方向判断必须由用户确认。
    """
    from .ocr import OcrEngineNoCls

    raw = OcrEngineNoCls.get()
    out: list[DirectionResult] = []
    total = len(pages_1based)
    for i, p in enumerate(pages_1based):
        if cancel_cb and cancel_cb():
            break
        res = DirectionResult(page=p, qualities={})
        try:
            if not raw.available:
                raise RuntimeError(raw.error or "方向分析 OCR 引擎不可用")
            # 始终评估完整四方向（0° 复用已渲染的图，其余按角度渲染）
            lines0: list = []
            for angle in (0, 90, 180, 270):
                if cancel_cb and cancel_cb():
                    break
                renderer.render(
                    p - 1, width_px=1000, user_rotation=angle, bucket="ocr4")
                lines = ocr_cache.recognize_cached(
                    raw, "nocls", renderer, p - 1, 1000, angle)
                if angle == 0:
                    lines0 = lines
                res.qualities[angle] = ocrfields.direction_quality(
                    {angle: lines})[angle]
                q0 = res.qualities[angle]
                if fast and angle == 0 and q0["quality"] >= .65 and q0["avg_score"] >= .9 and q0.get("horizontal_frac", 0) >= .95:
                    break  # clear upright text; ambiguous pages still get all directions

            cands = (0, 90, 180, 270)
            best, _ = ocrfields.best_direction(res.qualities)
            res.suggested_angle = best
            res.confidence = ocrfields.direction_confidence(res.qualities)
            kind, frac = ocrfields.line_orientation(lines0)
            parts = " / ".join(
                f"{a}°:{res.qualities[a]['n_lines']}行@{res.qualities[a]['avg_score']}"
                for a in sorted(res.qualities)
            )
            if best is None:
                res.suggestion_note = "四方向均未识别出文字，请人工确认"
            else:
                side = {"horizontal": "横", "vertical": "竖", "mixed": "混合",
                        "none": "未知"}[kind]
                res.suggestion_note = (
                    f"建议方向 {best}°（置信度 {res.confidence:.2f}；"
                    f"行向{side} {frac:.0%}；{parts}）"
                )
        except Exception as e:
            res.suggestion_note = f"分析失败：{e}"
        out.append(res)
        if progress_cb:
            progress_cb(i + 1, total)
    return out


def boundary_review(
    doc: ScanDocument,
    renderer,
    engine,
    progress_cb=None,
    cancel_cb=None,
    scan_internal: bool = True,
) -> list[BoundaryStatus]:
    """拆分边界双重验证。

    1) 每个分隔页后的下一页（新文档首页）做 OCR 标题特征检查：
       有标题特征 → 拆分可信；无 → 警告（疑似误拆，需人工确认）。
    2) 扫描组内页面（跳过分隔页附近）：发现强标题特征 → 建议新增拆分点。
    """
    out: list[BoundaryStatus] = []
    seps = doc.separators

    # 1) 每个分隔页后的下一页（新文档首页）
    after_sep: list[int] = []
    for s in sorted(seps):
        nxt = s + 1
        while nxt in seps:
            nxt += 1
        if nxt <= doc.page_count:
            after_sep.append(nxt)

    # 2) 组内页面抽样（跳过分隔页；页数少的组不扫）
    internal: list[int] = []
    if scan_internal:
        for g in doc.groups:
            if g.page_count <= 4:
                continue
            cand = [p for p in g.pages if p != g.start and p not in seps]
            if len(cand) > 6:
                cand = cand[:: len(cand) // 6][:6]
            internal.extend(cand)

    check_pages = after_sep + [p for p in internal if p not in after_sep]
    total = len(check_pages)
    after_sep_set = set(after_sep)
    for done, p in enumerate(check_pages, start=1):
        if cancel_cb and cancel_cb():
            break
        try:
            lines, w, h, _img = _ocr_page_rotation_aware(
                renderer, p - 1, engine, 1000, doc.pages[p - 1])
            score = ocrfields.title_likeness(lines, w, h)
        except Exception:
            score = 0.0
        if p in after_sep_set:
            if score >= CONFIRM_THRESHOLD:
                out.append(BoundaryStatus(
                    separator_page=p - 1, next_page=p, title_score=score,
                    status="ok", note=f"第 {p} 页具备标题特征（{score:.2f}），拆分可信",
                ))
            else:
                out.append(BoundaryStatus(
                    separator_page=p - 1, next_page=p, title_score=score,
                    status="warn", note=f"第 {p} 页无标题特征（{score:.2f}），拆分点可能不准确，请人工确认",
                ))
        elif score >= CONFIRM_THRESHOLD + 0.1:
            out.append(BoundaryStatus(
                separator_page=None, next_page=p, title_score=score,
                status="suggest_split", note=f"第 {p} 页具备标题特征（{score:.2f}），建议在此新增拆分点",
            ))
        if progress_cb:
            progress_cb(done, total)
    return out


def smart_split_review(
    doc: ScanDocument,
    renderer,
    engine,
    progress_cb=None,
    cancel_cb=None,
) -> list[BoundaryStatus]:
    """智能补拆复核：无空白页分隔场景的自动拆分（页码 + 标题双重验证）。

    对每个非分隔页 OCR 一次（复用回正识别），同时提取：
    1) 标题特征分数（ocrfields.title_likeness）；
    2) 页面印刷页码（pagenum.extract_page_number，页脚优先、兼容页眉）。

    双重验证规则（保守优先，绝不凭单一信号自动改动分组）：
    - 页码重置 + 标题特征 → status="auto_split"（UI 自动补拆，可撤销）；
    - 仅其一 → status="suggest_split"（人工确认，一键补拆）；
    - 已有分隔页后的首页 → ok / warn（语义同 boundary_review）。
    """
    seps = doc.separators
    starts = set(doc.split_starts)  # 已是文档起点的页不再重复评估
    scan_pages = [p for p in range(1, doc.page_count + 1) if p not in seps and p not in starts]
    after_sep: set[int] = set(starts)
    for s in sorted(seps):
        nxt = s + 1
        while nxt in seps:
            nxt += 1
        if nxt <= doc.page_count:
            after_sep.add(nxt)

    numbers: dict[int, int | None] = {}
    scores: dict[int, float] = {}
    structs: dict[int, dict] = {}  # 首页结构特征（用于否决误拆，见下）
    total = len(scan_pages)
    for done, p in enumerate(scan_pages, start=1):
        if cancel_cb and cancel_cb():
            break
        lines: list = []
        h = 1.0
        try:
            lines, _w, h, _img = _ocr_page_rotation_aware(
                renderer, p - 1, engine, 1000, doc.pages[p - 1])
            scores[p] = ocrfields.title_likeness(lines, _w, h)
            structs[p] = ocrfields.first_page_structure(lines, _w, h)
        except Exception:
            scores[p] = 0.0
            structs[p] = {}
        numbers[p] = pagenum.extract_page_number(lines, h) if lines else None
        if progress_cb:
            progress_cb(done, total)

    def _looks_like_doc_start(p: int) -> bool:
        """结构否决（v1.9-nightly P0 修复）：页码重置 + 标题分高**仍不足以**自动补拆，
        还需该页在结构上**确实像一份新文档的首面**。

        反例（A 套实测误拆）：文档内部"续表/续页"（has_continuation）、
        或仅重印文头 + 顶部分节标题（has_title=False 且 has_section=True）的页面，
        其页码会因页脚 `-2-`/`-3-` 与跨文档比较而误判"重置"，进而把一份正确文档切成两半。
        真正的新文档首面必然带标题（has_title=True）且不是"续表/续页"（has_continuation=False）。
        """
        st = structs.get(p) or {}
        return bool(st.get("has_title")) and not st.get("has_continuation")

    resets = set(pagenum.reset_points(numbers))
    out: list[BoundaryStatus] = []
    strong = CONFIRM_THRESHOLD + 0.1
    for p in scan_pages:
        if p == 1:
            continue  # 第 1 页永远是文档起点，无需评估；仅作为页码比较基准
        t_sig = scores.get(p, 0.0) >= strong
        n_sig = p in resets
        num = numbers.get(p)
        start_ok = _looks_like_doc_start(p)
        if p in after_sep:
            if t_sig:
                out.append(BoundaryStatus(
                    separator_page=p - 1, next_page=p, title_score=scores[p],
                    status="ok", note=f"第 {p} 页具备标题特征（{scores[p]:.2f}），拆分可信",
                ))
            else:
                out.append(BoundaryStatus(
                    separator_page=p - 1, next_page=p, title_score=scores[p],
                    status="warn", note=f"第 {p} 页无标题特征（{scores[p]:.2f}），拆分点可能不准确，请人工确认",
                ))
        elif t_sig and n_sig and start_ok:
            out.append(BoundaryStatus(
                separator_page=None, next_page=p, title_score=scores[p],
                status="auto_split", page_num=num,
                note=f"第 {p} 页页码重置（识别页码 {num}）且具备标题特征（{scores[p]:.2f}），双信号一致",
            ))
        elif t_sig and n_sig:
            # 双信号齐备但结构不像新文档首面（续表/无标题）→ 降级为人工确认，不自动补拆
            st = structs.get(p) or {}
            why = "含“续表/续页”标记" if st.get("has_continuation") else "顶部无标题特征"
            out.append(BoundaryStatus(
                separator_page=None, next_page=p, title_score=scores[p],
                status="suggest_split", page_num=num,
                note=f"第 {p} 页页码重置（识别页码 {num}）但{why}，疑似文档内部续页，建议人工确认",
            ))
        elif t_sig or n_sig:
            if t_sig:
                note = f"第 {p} 页具备标题特征（{scores[p]:.2f}），未见页码重置，建议人工确认后拆分"
            else:
                note = f"第 {p} 页页码重置（识别页码 {num}），标题特征不足（{scores[p]:.2f}），建议人工确认后拆分"
            out.append(BoundaryStatus(
                separator_page=None, next_page=p, title_score=scores[p],
                status="suggest_split", note=note, page_num=num,
            ))
    return out
