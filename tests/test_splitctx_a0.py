"""阶段 A0 回归：内部空白保留 + 上下文判定 + 导出双层核对。

覆盖点（全部合成样本，绝不针对特定测试页码硬编码）：
1. 上下文判定（多证据模型）：
   - 后一内容页印刷页码=1 → separator（页码重置强证据）；
   - 前/后内容页印刷页码 >1 → internal_blank（延续页码强证据）；
   - 后一内容页含"续表/续页"标记 → internal_blank（与首页结构矛盾）；
   - 后一内容页具备文档首面结构（标题最大字号 / 标题后主送机关）→ separator；
   - 证据不足或矛盾 → uncertain（默认保留）；
2. auto_group(context_aware=True) 只排除 separator 类，internal_blank/uncertain 保留；
3. 人工已改过的分隔标记永不被自动结果覆盖；
4. 导出业务完整性核对：uncertain/internal_blank 页必须留在输出内，禁止静默丢页；
5. 正常空白分隔页仍被正确识别（不因保护内部空白而整体失效）。
"""

from __future__ import annotations

import pytest

from scansplit import splitctx
from scansplit.grouping import auto_group, refresh_groups
from scansplit.model import BLANK, CONTENT, BlankResult, ScanDocument
from scansplit.splitctx import INTERNAL_BLANK, SEPARATOR, UNCERTAIN

pytestmark = pytest.mark.skipif(
    not __import__("tests.ocrimg", fromlist=["fonts_available"]).fonts_available(),
    reason="需要中文字体（Windows 自带）",
)


# --------------------------------------------------------------------------- #
# 合成"上下文引擎"：绕过真实 OCR，按页返回预设 OCR 行（页码 + 顶部结构）
# --------------------------------------------------------------------------- #

class PageSpec:
    """单页模拟内容：印刷页码 + 顶部结构行。

    结构行用 (text, font_px, centered) 描述；居中且字号大的行构成"标题"，
    "\x00page" 表示页脚印刷页码行。
    """

    def __init__(self, page_number: int | None = None,
                 title: str | None = None, title_px: int = 60,
                 body_px: int = 30, recipient: str | None = None,
                 section: str | None = None, continuation: str | None = None,
                 header: str | None = None) -> None:
        self.page_number = page_number
        self.title = title
        self.title_px = title_px
        self.body_px = body_px
        self.recipient = recipient
        self.section = section
        self.continuation = continuation
        self.header = header


class FakeRenderer:
    """记录 render 的页序，供 FakeEngine 按页返回预设行。"""

    def __init__(self, specs: dict[int, PageSpec], h: float = 1000.0) -> None:
        self.specs = specs          # {1-based page: PageSpec}
        self.h = h
        self.calls: list[int] = []

    def render(self, idx0: int, width_px: int = 1000, bucket: str = "ctx"):
        from PIL import Image

        self.calls.append(idx0 + 1)
        return Image.new("RGB", (800, int(self.h)), "white")

    def close(self):
        pass


class FakeEngine:
    """按页号 + 预设 spec 生成 OCR 行（使用真实 OcrLine）。"""

    def __init__(self, renderer: FakeRenderer) -> None:
        self.renderer = renderer

    def recognize(self, img):
        from scansplit.ocr import OcrLine

        page = self.renderer.calls[-1] if self.renderer.calls else 0
        spec = self.renderer.specs.get(page)
        if spec is None:
            return []
        h = self.renderer.h
        w = 800.0

        def line(text: str, y: float, hpx: float, centered: bool) -> "OcrLine":
            x0 = (w - 300) / 2 if centered else 60.0
            box = [(x0, y), (x0 + 300, y), (x0 + 300, y + hpx), (x0, y + hpx)]
            return OcrLine(text=text, score=0.9, box=box)

        lines = []
        y = 0.05 * h
        if spec.header:
            lines.append(line(spec.header, y, spec.body_px, True))
            y += spec.body_px * 2.4
        if spec.title:
            lines.append(line(spec.title, y, spec.title_px, True))
            y += spec.title_px * 1.8
        if spec.recipient:
            lines.append(line(spec.recipient, y, spec.body_px, False))
            y += spec.body_px * 2.2
        if spec.section:
            lines.append(line(spec.section, y, spec.body_px, False))
            y += spec.body_px * 2.2
        if spec.continuation:
            lines.append(line(spec.continuation, y, spec.body_px, True))
            y += spec.body_px * 2.2
        # 填充正文行（避免中位数被顶部大字带偏）
        for i in range(6):
            lines.append(line(f"正文内容行 {i}", y, spec.body_px, False))
            y += spec.body_px * 2.0
        if spec.page_number is not None:
            lines.append(line(f"第 {spec.page_number} 页", 0.90 * h, spec.body_px, True))
        return lines


def _blank_ok() -> BlankResult:
    return BlankResult(verdict=BLANK, confidence=0.95, signals={})


def _content_ok() -> BlankResult:
    return BlankResult(verdict=CONTENT, confidence=0.9, signals={})


def _doc_with(kinds: list[str], specs: dict[int, "PageSpec"] | None = None):
    """构造一个 ScanDocument（无真实文件依赖：直接装配 PageState）。

    kinds: 'c' = 内容页，'b' = 视觉空白候选页。
    specs: {页(1起): PageSpec}，缺省页返回空 OCR。
    """
    import os
    import tempfile

    import pypdf

    from tests.samplegen import build_scan_pdf

    kinds_img = ["text" if k == "c" else "blank" for k in kinds]
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    build_scan_pdf(tmp.name, kinds_img)
    reader = pypdf.PdfReader(tmp.name)
    doc = ScanDocument(tmp.name, reader, [])
    doc._tmp_path = tmp.name  # 便于清理
    for st, k in zip(doc.pages, kinds):
        st.blank = _blank_ok() if k == "b" else _content_ok()
    return doc


def _judge(doc, specs):
    renderer = FakeRenderer(specs)
    engine = FakeEngine(renderer)
    return splitctx.judge_blank_context(doc, renderer, engine)


# --------------------------------------------------------------------------- #
# 1. 上下文判定规则
# --------------------------------------------------------------------------- #

def test_next_page_number_one_is_separator():
    """后一内容页印刷页码=1 → 分隔空白（页码重置强证据）。"""
    # c b c  ：第 2 页空白；其后第 3 页页码=1（新文档首页）
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(page_number=3), 3: PageSpec(page_number=1)})
    assert len(evs) == 1
    assert evs[0].page == 2 and evs[0].verdict == SEPARATOR


def test_prev_page_mid_document_is_internal_blank():
    """前一内容页页码 >1（文档已在进行中）→ 内部空白。"""
    # c b c ：第 2 页空白；前页页码=3、后页页码=4（连续延续）
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(page_number=3), 3: PageSpec(page_number=4)})
    assert evs[0].verdict == INTERNAL_BLANK


def test_next_page_mid_document_is_internal_blank():
    """后一内容页页码 >1（延续页码）→ 内部空白。"""
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(), 3: PageSpec(page_number=6)})
    assert evs[0].verdict == INTERNAL_BLANK


def test_continuation_marker_beats_title():
    """后一内容页带“续表/（续）”标记 → 内部空白（与首页标题结构矛盾时以延续为准）。"""
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {
        1: PageSpec(),
        3: PageSpec(title="实验耗材领用管理台账", title_px=60,
                    continuation="（续表）", page_number=None),
    })
    assert evs[0].verdict == INTERNAL_BLANK


def test_first_page_structure_title_is_max_is_separator():
    """后一内容页标题为全页最大字号（文档首面结构）→ 分隔空白。"""
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {
        1: PageSpec(section="一、检查内容"),
        3: PageSpec(title="关于开展教学检查的通知", title_px=64, body_px=30),
    })
    assert evs[0].verdict == SEPARATOR


def test_first_page_structure_recipient_is_separator():
    """标题后紧跟主送机关（文档首面结构）→ 分隔空白。"""
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {
        1: PageSpec(),
        3: PageSpec(title="有关情况报告", title_px=56, recipient="各有关单位：",
                    header="后勤保障处文件"),
    })
    assert evs[0].verdict == SEPARATOR


def test_table_fake_pagenum_with_new_title_is_separator():
    """表格数字假页码 + 无续接后缀 + 与前一内容页标题不同源 → 分隔空白。

    真实场景（81 页压力件 p11/p12）：分隔页后是侧放表格首页，其页码被
    误取为表格数字（如 18），标题为表名（全页最大字号）。此时应识别为
    新文档首页，从而删除前隔页，而非把整个文档粘连。
    """
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {
        1: PageSpec(title="上一份文档标题", title_px=60, body_px=30),
        3: PageSpec(title="教学仪器设备季度统计表", title_px=64, body_px=30,
                    page_number=18),
    })
    assert evs[0].verdict == SEPARATOR


def test_table_same_title_continuation_page_is_internal_blank():
    """后页虽带"假页码"且为强首面结构，但与前一内容页标题同源 → 内部续页，保留。

    真实场景（81 页压力件 p57→p58→p59）：同一张表格文档内部出现空白页，
    前后内容页重印同一表名；此时绝不可判为分隔页，否则会把表格文档拆散。
    """
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {
        1: PageSpec(title="实验教学中心巡查记录表页次：4", title_px=60, body_px=30),
        3: PageSpec(title="实验教学中心巡查记录表", title_px=64, body_px=30,
                    page_number=18),
    })
    assert evs[0].verdict == INTERNAL_BLANK


def test_section_heading_without_title_is_uncertain():
    """顶部只有序号小节标题、无文档首面结构 → uncertain（保守保留）。"""
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(), 3: PageSpec(section="二、相关情况说明")})
    assert evs[0].verdict == UNCERTAIN


def test_no_page_number_signal_is_uncertain():
    """两侧都无页码信号 → uncertain（默认保留），绝不擅判分隔。"""
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(), 3: PageSpec()})
    assert evs[0].verdict == UNCERTAIN
    assert evs[0].page == 2


def test_trailing_blank_is_separator():
    """文件末尾空白（其后无内容页）→ 边界分隔。"""
    doc = _doc_with(["c", "c", "b"])
    evs = _judge(doc, {1: PageSpec(page_number=1), 2: PageSpec(page_number=2)})
    assert evs[0].page == 3 and evs[0].verdict == SEPARATOR


def test_leading_blank_is_separator():
    """文件开头空白（其前无内容页，后页也无页码重置信号）→ 边界分隔。"""
    doc = _doc_with(["b", "c", "c"])
    evs = _judge(doc, {2: PageSpec(), 3: PageSpec()})
    assert evs[0].page == 1 and evs[0].verdict == SEPARATOR


# --------------------------------------------------------------------------- #
# 2. auto_group 只排除 separator
# --------------------------------------------------------------------------- #

def test_auto_group_keeps_internal_blank_and_uncertain():
    """context_aware=True：内部空白与 uncertain 都保留在文档内，仅 separator 被排除。"""
    # 1(c) 2(b:internal) 3(c) 4(b:separator) 5(c)
    doc = _doc_with(["c", "b", "c", "b", "c"])
    evs = _judge(doc, {
        1: PageSpec(page_number=3),
        3: PageSpec(page_number=4),
        5: PageSpec(page_number=1),
    })  # 第 2 页：中段 → internal；第 4 页：后页=1 → separator
    splitctx.apply_context(doc, evs)
    assert doc.pages[1].blank_context == INTERNAL_BLANK
    assert doc.pages[3].blank_context == SEPARATOR

    groups = auto_group(doc, context_aware=True)
    assert [(g.start, g.end) for g in groups] == [(1, 3), (5, 5)]
    # 内部空白第 2 页仍在文档内（未被排除）
    assert 2 in doc.groups[0].pages
    assert doc.separators == {4}


def test_auto_group_legacy_mode_excludes_all_blanks():
    """context_aware=False 退回旧行为（全部空白候选作分隔），供兼容/对照。"""
    doc = _doc_with(["c", "b", "c"])
    auto_group(doc, context_aware=False)
    assert doc.separators == {2}


def test_uncertain_blank_kept_by_default():
    """uncertain 页默认保留（sep=False），不得作为分隔排除。"""
    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(), 3: PageSpec()})
    assert evs[0].verdict == UNCERTAIN
    splitctx.apply_context(doc, evs)
    auto_group(doc, context_aware=True)
    assert doc.pages[1].separator is False
    assert 2 in doc.groups[0].pages


# --------------------------------------------------------------------------- #
# 3. 人工确认优先
# --------------------------------------------------------------------------- #

def test_manual_separator_never_overwritten():
    """人工已改过的页，自动上下文判定不得覆盖其分隔状态。"""
    doc = _doc_with(["c", "b", "c"])
    # 用户手工把第 2 页标为分隔
    doc.pages[1].separator = True
    doc.manual_separator_pages.add(2)
    evs = _judge(doc, {1: PageSpec(page_number=3), 3: PageSpec(page_number=4)})  # 上下文认为它更像内部空白
    assert evs[0].verdict == INTERNAL_BLANK
    splitctx.apply_context(doc, evs)
    # 人工标记仍保留
    assert doc.pages[1].separator is True
    auto_group(doc, context_aware=True)
    assert doc.pages[1].separator is True


# --------------------------------------------------------------------------- #
# 4. 导出业务完整性核对
# --------------------------------------------------------------------------- #

def test_business_integrity_flags_silent_drop():
    """若 uncertain 页被误排除，业务完整性核对必须报出问题（fail-closed）。"""
    from scansplit.exporter import business_integrity_issues

    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(), 3: PageSpec()})
    splitctx.apply_context(doc, evs)
    auto_group(doc, context_aware=True)  # unknown → 保留，无问题
    assert business_integrity_issues(doc) == []

    # 人为制造"静默丢页"：把 uncertain 页错误地设为分隔页（且未经人工确认）
    doc.pages[1].separator = True
    refresh_groups(doc)
    issues = business_integrity_issues(doc)
    assert issues, "未经确认排除 uncertain 页必须被业务完整性核对拦截"
    assert any("第 2 页" in s for s in issues)


def test_business_integrity_ok_when_separator_confirmed():
    """真正的分隔页（上下文判定 separator）排除后，业务完整性核对通过。"""
    from scansplit.exporter import business_integrity_issues

    doc = _doc_with(["c", "b", "c"])
    evs = _judge(doc, {1: PageSpec(page_number=3), 3: PageSpec(page_number=1)})  # 后页页码=1 → separator
    assert evs[0].verdict == SEPARATOR
    splitctx.apply_context(doc, evs)
    auto_group(doc, context_aware=True)
    assert business_integrity_issues(doc) == []


# --------------------------------------------------------------------------- #
# 5. 正常分隔页仍能识别（不得整体失效）
# --------------------------------------------------------------------------- #

def test_normal_separators_still_detected():
    """多份文档、标准空白分隔场景：所有真分隔页都被识别，内部空白保留。"""
    # 文档1: 1(c) 2(b:int) 3(c) | 4(b:sep) | 文档2: 5(c) 6(b:int) 7(c)
    doc = _doc_with(["c", "b", "c", "b", "c", "b", "c"])
    evs = _judge(doc, {
        1: PageSpec(page_number=5),
        3: PageSpec(page_number=6),
        5: PageSpec(page_number=1),
        7: PageSpec(page_number=2),
    })
    ctx = {e.page: e.verdict for e in evs}
    assert ctx[2] == INTERNAL_BLANK   # 第 2 页：前页页码=5 → 内部
    assert ctx[4] == SEPARATOR        # 第 4 页：后页页码=1 → 分隔
    assert ctx[6] == INTERNAL_BLANK   # 第 6 页：前页页码=1、后页=2 → 内部
    splitctx.apply_context(doc, evs)
    auto_group(doc, context_aware=True)
    assert doc.separators == {4}
    assert [(g.start, g.end) for g in doc.groups] == [(1, 3), (5, 7)]
