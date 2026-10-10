"""OCR 自动命名编排：标题/单位提取、横向页方向分析、拆分边界双重验证。

- 空白页检测是拆分的第一依据；OCR 首页标题只作为第二验证信号，
  两者不一致时必须用户确认。
- OCR 结果全部来自实际识别文字，识别失败不编造，字段标记"待确认"。
- OCR 只读取渲染图像，绝不写回 PDF。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import naming, ocrfields, pagenum
from .model import ScanDocument

CONFIRM_THRESHOLD = 0.55  # 低于此置信度标记"待确认"


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


@dataclass
class DirectionResult:
    page: int
    qualities: dict  # angle -> {n_lines, avg_score, quality}
    suggested_angle: int | None = None
    suggestion_note: str = ""


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


def _ocr_page_rotation_aware(renderer, idx0: int, engine, width_px: int):
    """识别页面文字，返回 (lines, w, h, img)。

    内容正放直接用常规引擎；内容侧放（检测框竖行为主）时用禁用 cls 的
    引擎在 90/270 中选质量更高者——仅用于识别分析，绝不写回 PDF、绝不
    自动设置页面旋转。img 为与 lines 对应的渲染图像（印章提取用）。
    """
    img = renderer.render(idx0, width_px=width_px, bucket="ocr")
    lines = engine.recognize(img)
    w, h = img.size
    kind, _frac = ocrfields.line_orientation(lines)
    if kind != "vertical":
        return lines, w, h, img
    from .ocr import OcrEngineNoCls

    raw = OcrEngineNoCls.get()
    if not raw.available:
        return lines, w, h, img  # 无引擎可判断，保留 0° 结果（待确认兜底）
    best_lines, best_wh, best_img, best_q = None, None, img, -1.0
    for angle in (90, 270):
        img2 = renderer.render(idx0, width_px=width_px, user_rotation=angle, bucket="ocr-rot")
        ls = raw.recognize(img2)
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
) -> list[NameResult]:
    """为每份分组自动提取标题与发文单位并填入组。

    engine 为 None 时仅走文本层路径（无 OCR 环境）。
    """
    results: list[NameResult] = []
    total = len(doc.groups)
    for gi, g in enumerate(doc.groups):
        if cancel_cb and cancel_cb():
            break
        res = NameResult(group_index=gi)
        first0, last0 = g.start - 1, g.end - 1

        # 1) 文本层（可选增强）
        if _has_text_layer(doc.reader, first0):
            res.title_candidates = _page_text_lines(doc.reader, first0)
            last_titles = naming.extract_unit_candidates(doc.reader, last0)
            for t in last_titles:
                res.unit_candidates.append(
                    ocrfields.FieldCandidate(t, 0.85, "文本层", reason="末页文本层提取")
                )
            res.used_ocr = False
        elif engine is not None and engine.available:
            res.used_ocr = True
            try:
                lines, w, h, img1 = _ocr_page_rotation_aware(renderer, first0, engine, 1200)
                res.title_candidates = ocrfields.extract_title(lines, w, h)
                first_units = ocrfields.extract_unit_first_page(lines, w, h)
                if last0 != first0:
                    last_lines, lw, lh, img_last = _ocr_page_rotation_aware(
                        renderer, last0, engine, 1200
                    )
                    last_units = ocrfields.extract_unit_last_page(last_lines, lw, lh)
                else:
                    # 单页文档：同一页既是首页也是末页（落款就在首页上）
                    last_units = ocrfields.extract_unit_last_page(lines, w, h)
                    img_last = img1
                seal_units: list = []
                try:
                    from . import ocrseal

                    seal_units = ocrseal.extract_seal_units(img1, engine)
                    if last0 != first0:
                        seal_units += ocrseal.extract_seal_units(img_last, engine)
                except Exception:
                    seal_units = []  # 印章识别失败不影响主流程
                res.unit_candidates = ocrfields.merge_unit_candidates(
                    first_units, last_units, seal_units
                )
            except Exception as e:
                res.error = str(e)

        # 2) 填入组字段
        best_t = res.best_title
        best_u = res.best_unit
        if best_t and best_t.confidence >= CONFIRM_THRESHOLD:
            g.title = best_t.text
            g.title_conf = best_t.confidence
        else:
            g.title = best_t.text if best_t else ""
            g.title_conf = best_t.confidence if best_t else None
        if best_u and best_u.confidence >= CONFIRM_THRESHOLD:
            g.unit = best_u.text
            g.unit_conf = best_u.confidence
        else:
            g.unit = best_u.text if best_u else ""
            g.unit_conf = best_u.confidence if best_u else None
        g.title_candidates = [c.to_dict() for c in res.title_candidates]
        g.unit_candidates = [c.to_dict() for c in res.unit_candidates]
        g.need_confirm = (
            not res.title_candidates
            or not res.unit_candidates
            or (best_t and best_t.confidence < CONFIRM_THRESHOLD)
            or (best_u and best_u.confidence < CONFIRM_THRESHOLD)
            or (best_u and best_u.source == "印章")  # 印章文字可靠性有限，须人工确认
        )
        results.append(res)
        if progress_cb:
            progress_cb(gi + 1, total)
    return results


def analyze_directions(
    doc: ScanDocument,
    renderer,
    pages_1based: list[int],
    progress_cb=None,
    cancel_cb=None,
) -> list[DirectionResult]:
    """对指定页面做 0/90/180/270 四方向 OCR 质量对比，辅助判断正确方向。

    两类信号（缺一不可）：
    1) 0° 检测框几何（横/竖行占比）：det 框四点序纯几何，可靠区分
       "内容正放（横行为主）"与"内容侧放（竖行为主）"，但不分 0/180、90/270；
    2) 禁用 cls 的 OCR 质量：cls 模型会把倒置/侧放文字自动翻转识别，
       常规引擎对四方向都能读通（质量无区分度）；禁用 cls 后倒置文字
       质量骤降，即可在候选对（{0,180} 或 {90,270}）内选出正确方向。

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
            img = renderer.render(p - 1, width_px=1000, user_rotation=0, bucket="ocr4")
            lines0 = raw.recognize(img)
            res.qualities[0] = ocrfields.direction_quality({0: lines0})[0]
            kind, frac = ocrfields.line_orientation(lines0)
            cands = (90, 270) if kind == "vertical" else (0, 180)
            for angle in cands:
                if angle == 0:
                    continue
                img = renderer.render(
                    p - 1, width_px=1000, user_rotation=angle, bucket="ocr4"
                )
                lines = raw.recognize(img)
                res.qualities[angle] = ocrfields.direction_quality({angle: lines})[angle]
            best, _ = ocrfields.best_direction({a: res.qualities[a] for a in cands})
            res.suggested_angle = best
            parts = " / ".join(
                f"{a}°:{res.qualities[a]['n_lines']}行@{res.qualities[a]['avg_score']}"
                for a in sorted(res.qualities)
            )
            if best is None:
                res.suggestion_note = "四方向均未识别出文字，请人工确认"
            else:
                side = {"horizontal": "横", "vertical": "竖", "mixed": "混合"}[kind]
                res.suggestion_note = (
                    f"建议方向 {best}°（行向{side} {frac:.0%}；{parts}）"
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
            lines, w, h, _img = _ocr_page_rotation_aware(renderer, p - 1, engine, 1000)
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
    total = len(scan_pages)
    for done, p in enumerate(scan_pages, start=1):
        if cancel_cb and cancel_cb():
            break
        lines: list = []
        h = 1.0
        try:
            lines, _w, h, _img = _ocr_page_rotation_aware(renderer, p - 1, engine, 1000)
            scores[p] = ocrfields.title_likeness(lines, _w, h)
        except Exception:
            scores[p] = 0.0
        numbers[p] = pagenum.extract_page_number(lines, h) if lines else None
        if progress_cb:
            progress_cb(done, total)

    resets = set(pagenum.reset_points(numbers))
    out: list[BoundaryStatus] = []
    strong = CONFIRM_THRESHOLD + 0.1
    for p in scan_pages:
        if p == 1:
            continue  # 第 1 页永远是文档起点，无需评估；仅作为页码比较基准
        t_sig = scores.get(p, 0.0) >= strong
        n_sig = p in resets
        num = numbers.get(p)
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
        elif t_sig and n_sig:
            out.append(BoundaryStatus(
                separator_page=None, next_page=p, title_score=scores[p],
                status="auto_split", page_num=num,
                note=f"第 {p} 页页码重置（识别页码 {num}）且具备标题特征（{scores[p]:.2f}），双信号一致",
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
