"""拆分上下文判定：把"单页视觉空白"升级为"页面业务属性"。

背景（v1.9 阶段 A0）：
- 单页像素检测（blankdetect）只能回答"这页看起来是不是空白"（视觉属性：
  blank / maybe / content）；
- 但它无法区分两种本质不同的空白：
  * **分隔空白**：两份文档之间的空白页，导出时应删除（业务属性 separator）；
  * **内部空白**：一份文档内部双面件的背面空白，必须保留（业务属性 internal_blank）。
- 实测（四个 100 页样本，57 个空白候选）证明：内部空白与分隔空白的**单页像素统计
  完全相同**（ink≈0、dark≈0、mean≈241.6），单页证据不可能区分；唯一可靠信号是
  **上下文**——相邻内容页在文档中的位置与结构。

多证据模型（全部语义化、非样本专用；**多证据矛盾则降级为 uncertain**）：

证据 A（印刷页码重置）：
  * 后一内容页印刷页码 == 1 → 强 separator 证据；
  * 后一内容页印刷页码 > 1（延续页码）→ 强 internal 证据。
  * 注意：**前一内容页的页码不可用作 internal 证据**——分隔空白的前一页本就是
    上一份文档的末页，天然携带中段页码（实测 25/50 分隔页满足 prev_pn>1）。
证据 B（后一内容页是否像"文档首面"结构）：
  * 顶部带存在大字号居中标题行（has_title）→ 支持 separator；
  * 标题即全页最大字号（title_is_max）或其后紧跟主送机关（has_recipient）
    → 加强 separator；
  * 存在"续表/续页/（续）"标记（has_continuation）→ 强 internal 证据。

证据 C（前后内容页标题同源）：后一内容页与最近的前一内容页标题去噪后
相同/互为前后缀/一方含另一方 → 后页是同一份文档的续页（表格文档续页
常重印表名），否决 separator。

上下文 OCR 方向感知：内容页先 0° 识别，侧放页（检测框竖行为主）自动在
90/270 中选质量更高者回正后再提取上述证据——侧放表格首页的标题/页码
只有回正后才能读到（真实样本实测）。

判定（保守优先；证据不足或矛盾一律 uncertain，默认保留）：
  1. 后页页码 == 1 → separator；
  2. 后页页码 > 1 且为强文档首面结构（标题最大 / 有主送机关）、无续接
     后缀、且与前一内容页标题不同源 → 该"页码"疑为表格数字（真实样本
     侧放表格首页实测），判 separator；后页页码 > 1 的其他情形 → internal_blank；
  3. 否则若存在"续表/续页/（续）"标记 → internal_blank；
  4. 否则若后页具备文档首面结构（标题最大 / 有主送机关）→ separator；
  5. 否则 → uncertain（保留，交人工/复核）。

约束：本模块只读取渲染图做 OCR，**绝不修改 PDF**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 业务属性枚举
SEPARATOR = "separator"
INTERNAL_BLANK = "internal_blank"
UNCERTAIN = "uncertain"

CTX_LABEL = {
    SEPARATOR: "分隔页",
    INTERNAL_BLANK: "内部空白（保留）",
    UNCERTAIN: "空白-待确认（默认保留）",
}


@dataclass
class ContextEvidence:
    """单个空白候选页的上下文证据，供 UI 展示与测试断言。"""

    page: int                                   # 1 起始
    verdict: str = UNCERTAIN                    # separator/internal_blank/uncertain
    next_content_page: int | None = None
    prev_content_page: int | None = None
    next_page_number: int | None = None         # 后一内容页识别到的印刷页码
    prev_page_number: int | None = None         # 前一内容页识别到的印刷页码
    next_title_likeness: float = 0.0
    prev_title_likeness: float = 0.0
    # 后一内容页的首页结构特征（见 ocrfields.first_page_structure）
    next_structure: dict | None = None
    # 前一内容页的首页结构特征（用于"前后页标题同源"判定）
    prev_structure: dict | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "page": self.page, "verdict": self.verdict,
            "next_content_page": self.next_content_page,
            "prev_content_page": self.prev_content_page,
            "next_page_number": self.next_page_number,
            "prev_page_number": self.prev_page_number,
            "next_title_likeness": round(self.next_title_likeness, 3),
            "prev_title_likeness": round(self.prev_title_likeness, 3),
            "next_structure": dict(self.next_structure or {}),
            "prev_structure": dict(self.prev_structure or {}),
            "reason": self.reason,
        }


# 标题归一化：去掉页码/页次等噪声后缀与标点空白，便于"同源"比较。
_TITLE_NOISE_RE = re.compile(r"页次[：:]\s*\d+|第\s*\d+\s*页|共\s*\d+\s*页|[（(]\s*(?:统计|数据)?\s*附\s*[表页件]\s*[）)]")


def _norm_title(t: str) -> str:
    t = _TITLE_NOISE_RE.sub("", t or "")
    return re.sub(r"[\s，。、；：,.;:()（）\-—_]+", "", t)


def _titles_same_source(a: str, b: str) -> bool:
    """判断两页标题是否"同源"（同一份文档的续页）。

    同源包括：完全相等、一方为另一方的（去噪后）前缀/后缀、或去掉续接后缀后相等。
    用于区分"表格文档内部续页（前后页同标题）"与"新文档首页（标题不同）"。
    """
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    # 短标题（≥5 字）是长标题的子串 → 视为同源（如"实验教学中心巡查记录表" ⊂ "…记录表（续表）"）
    if len(shorter) >= 5 and (shorter in longer):
        return True
    return False


def _nearest_content(doc, page_1based: int, step: int) -> int | None:
    """从 page 出发沿 step 方向找最近的非空白页（用于取上下文）。"""
    p = page_1based + step
    while 1 <= p <= doc.page_count:
        st = doc.pages[p - 1]
        if not (st.blank is not None and st.blank.verdict == "blank"):
            return p
        p += step
    return None


def _page_ocr(renderer, engine, page_1based: int, cache: dict) -> tuple:
    """对指定页做一次方向感知 OCR，返回 (lines, w, h)。

    先 0° 常规引擎识别（含 cls，只用于读取"页码/首页结构"，不用于方向判断）；
    若内容侧放（检测框竖行为主），用禁用 cls 的引擎在 90/270 中选质量更高者——
    侧放页的标题/页码/续表标记只有回正后才能正确提取（实测：分隔空白后紧跟
    侧放表格首页时，0° 下 has_title 恒为 False，导致真分隔页被判 uncertain）。
    带页内缓存，避免同一上下文页被前后多个空白候选重复识别。
    """
    from . import ocrfields  # 延迟导入，避免循环依赖

    if page_1based in cache:
        return cache[page_1based]
    try:
        img = renderer.render(page_1based - 1, width_px=1000, bucket="ctx")
        lines = engine.recognize(img)
        w, h = img.size
        kind, _frac = ocrfields.line_orientation(lines)
        if kind == "vertical":
            from .ocr import OcrEngineNoCls

            raw = OcrEngineNoCls.get()
            if raw.available:
                best_lines, best_wh, best_q = None, (w, h), -1.0
                for angle in (90, 270):
                    img2 = renderer.render(
                        page_1based - 1, width_px=1000, user_rotation=angle, bucket="ctx-rot")
                    ls = raw.recognize(img2)
                    q = ocrfields.direction_quality({angle: ls})[angle]["quality"]
                    if q > best_q:
                        best_lines, best_wh, best_q = ls, img2.size, q
                if best_lines is not None:
                    lines, w, h = best_lines, best_wh[0], best_wh[1]
    except Exception:
        lines, w, h = [], 1.0, 1.0
    cache[page_1based] = (lines, w, h)
    return cache[page_1based]


def judge_blank_context(
    doc,
    renderer,
    engine,
    blank_pages: list[int] | None = None,
    progress_cb=None,
    cancel_cb=None,
) -> list[ContextEvidence]:
    """对每个视觉空白候选页判定业务属性（separator / internal_blank / uncertain）。

    blank_pages 缺省时取 doc 中所有 verdict==blank 的页。返回与输入同序的证据列表。
    """
    from . import ocrfields, pagenum  # 延迟导入，避免循环依赖

    if blank_pages is None:
        blank_pages = [st.src_index for st in doc.pages
                       if st.blank is not None and st.blank.verdict == "blank"]

    cache: dict = {}
    out: list[ContextEvidence] = []
    total = len(blank_pages)
    for i, p in enumerate(blank_pages):
        if cancel_cb and cancel_cb():
            break
        ev = ContextEvidence(page=p)
        try:
            nxt = _nearest_content(doc, p, +1)
            prv = _nearest_content(doc, p, -1)
            ev.next_content_page = nxt
            ev.prev_content_page = prv

            if nxt is None:
                # 末尾空白（后面没有内容页）→ 文档边界，可排除
                ev.verdict = SEPARATOR
                ev.reason = "位于文件末尾，之后无内容页 → 视为末尾分隔空白"
                out.append(ev)
                if progress_cb:
                    progress_cb(i + 1, total)
                continue

            lns, w, h = _page_ocr(renderer, engine, nxt, cache)
            if lns:
                ev.next_page_number = pagenum.extract_page_number(lns, h)
                ev.next_title_likeness = ocrfields.title_likeness(lns, w, h)
                ev.next_structure = ocrfields.first_page_structure(lns, w, h)

            if prv is not None:
                plns, pw, ph = _page_ocr(renderer, engine, prv, cache)
                if plns:
                    ev.prev_page_number = pagenum.extract_page_number(plns, ph)
                    ev.prev_title_likeness = ocrfields.title_likeness(plns, pw, ph)
                    ev.prev_structure = ocrfields.first_page_structure(plns, pw, ph)

            # --- 多位证据判定 ---
            st = ev.next_structure or {}
            pst = ev.prev_structure or {}
            nxt_is_first = ev.next_page_number == 1
            nxt_mid = ev.next_page_number is not None and ev.next_page_number > 1
            has_cont = bool(st.get("has_continuation"))
            has_title = bool(st.get("has_title"))
            strong_title = has_title and (st.get("title_is_max") or st.get("has_recipient"))
            # "前后页标题同源"：后页与最近的前一内容页标题相同/互为前后缀
            # → 后页是同一份文档的续页（表格文档内部续页常重印表名）。
            same_src = (prv is not None) and _titles_same_source(
                st.get("title_text", ""), pst.get("title_text", ""))

            if nxt_is_first:
                # 证据 A：后一内容页页码明确重置为 1 → 新文档首页
                ev.verdict = SEPARATOR
                ev.reason = "后一内容页印刷页码=1（新文档首页）→ 分隔空白"
            elif nxt_mid and strong_title and not has_cont and not same_src:
                # 证据 A 被证据 B 否决：后页虽带延续页码，但该"页码"很可能来自表格数字
                # （如表格单元格里的 18/96/144），而页面本身是**无续接后缀**的强文档首面
                # （标题全页最大 / 标题后紧跟主送机关），且无"续表/续页/（统计附表）"标记，
                # 且与前一内容页标题**不同源**（不是同一文档重印表名的续页）
                # → 这不是文档中途的续页，而是新文档首页 → 前隔页应删。
                why = "标题为全页最大字号" if st.get("title_is_max") else "标题后紧跟主送机关"
                ev.verdict = SEPARATOR
                ev.reason = (
                    f"后页页码={ev.next_page_number} 疑为表格数字，而页面为无续接后缀、"
                    f"且与前一内容页标题不同源的文档首面结构（{why}）："
                    f"{st.get('title_text','')[:24]} → 分隔空白"
                )
            elif nxt_mid:
                # 证据 A：后一内容页为延续页码（>1）→ 文档已在进行中
                extra = "（后页与前一内容页标题同源，判为表格文档续页）" if same_src else ""
                ev.verdict = INTERNAL_BLANK
                ev.reason = f"后一内容页延续页码={ev.next_page_number} → 内部空白，保留{extra}"
            elif has_cont:
                # 证据 B 矛盾：明确"续表/续页/（续）"标记 → 内部继续
                ev.verdict = INTERNAL_BLANK
                ev.reason = "后一内容页含“续表/续页/（续）”标记 → 内部空白，保留"
            elif strong_title:
                # 证据 B：后一内容页具备文档首面结构（标题最大 / 有主送机关）
                why = "标题为全页最大字号" if st.get("title_is_max") else "标题后紧跟主送机关"
                ev.verdict = SEPARATOR
                ev.reason = f"后一内容页具备文档首面结构（{why}）：{st.get('title_text','')[:24]} → 分隔空白"
            elif prv is None:
                # 前面没有内容页（文件开头附近）→ 边界
                ev.verdict = SEPARATOR
                ev.reason = "位于文件开头，之前无内容页 → 视为边界空白"
            else:
                # 证据不足或互相矛盾：保守判 uncertain（默认保留），交人工/复核
                ev.verdict = UNCERTAIN
                parts = []
                if has_title:
                    parts.append("有标题但非最大字号且无主送机关")
                if st.get("has_section"):
                    parts.append("顶部含序号小节标题")
                note = "、".join(parts) if parts else "无明确首页/延续信号"
                ev.reason = (
                    f"{note}（后页标题特征 {ev.next_title_likeness:.2f}）"
                    "→ 证据不足，待人工确认，默认保留"
                )
        except Exception as e:  # OCR 失败 → 保守保留
            ev.verdict = UNCERTAIN
            ev.reason = f"上下文分析失败：{e}（默认保留）"
        out.append(ev)
        if progress_cb:
            progress_cb(i + 1, total)
    return out


def apply_context(
    doc,
    evidences: list[ContextEvidence],
    respect_manual: bool = True,
) -> dict:
    """把上下文判定结果写入 PageState。

    - **绝不覆盖人工已确认的分隔状态**（respect_manual=True 时跳过
      已被用户手工改过分隔标记的页；由调用方通过 doc 的 manual 集合传入）。
    - 只有 verdict==separator 才置 separator=True；internal_blank/uncertain 一律保留
      （separator=False，页留在文档内）。

    返回 {verdict: 页列表} 统计，便于日志与测试。
    """
    by_page = {e.page: e for e in evidences}
    counts = {SEPARATOR: [], INTERNAL_BLANK: [], UNCERTAIN: []}
    manual = getattr(doc, "manual_separator_pages", None) or set()
    for p, ev in by_page.items():
        st = doc.pages[p - 1]
        st.blank_context = ev.verdict
        if respect_manual and p in manual:
            continue  # 人工标记优先，不覆盖
        st.separator = (ev.verdict == SEPARATOR)
        counts[ev.verdict].append(p)
    return {k: sorted(v) for k, v in counts.items()}
