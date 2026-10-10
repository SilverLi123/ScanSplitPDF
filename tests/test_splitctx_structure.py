"""first_page_structure 与续页标记的纯逻辑测试（不需要 OCR / 字体）。

覆盖 v1.9 阶段 A0 引入的"文档首面结构"判别所需的边界情形：
  - "续"标记只认独立的 续表/续页/（续），绝不误伤"后续/继续工作要求"；
  - 文头（"XX处文件"）不得被当作文档标题；
  - 序号小节标题不得被当作标题。
"""

from scansplit.ocr import OcrLine
from scansplit.ocrfields import CONTINUATION_RE, first_page_structure


def _line(text: str, y0: float, h: float, cx: float, w: float = 1000.0) -> OcrLine:
    x0 = cx - 150.0
    box = [(x0, y0), (x0 + 300.0, y0), (x0 + 300.0, y0 + h), (x0, y0 + h)]
    return OcrLine(text=text, score=0.95, box=box)


# -- 续页标记正则（不得误伤正文） -------------------------------------------- #

def test_continuation_regex_true_markers():
    for t in ("（续表）", "(续)", "续表", "续页", "第 2 页续"):
        assert CONTINUATION_RE.search(t), f"应识别为续页标记：{t}"


def test_continuation_regex_never_matches_ordinary_text():
    """关键回归：正文中的"后续/继续"等绝不能被当作续页标记。"""
    for t in ("一、后续工作要求", "二、相关情况说明", "继续推进整改工作",
              "实验室危险化学品使用情况登记表", "设备入库验收及使用情况明细表",
              "关于组织年度岗位考核工作的通知"):
        assert not CONTINUATION_RE.search(t), f"误判为续页标记：{t}"


# -- 首面结构 --------------------------------------------------------------- #

_W, _H = 1000.0, 1000.0


def test_first_page_structure_document_first_page():
    """标准文档首面：文头 + 大标题 + 主送机关 → has_title / has_recipient。"""
    lines = [
        _line("后勤保障处文件", 60, 36, _W / 2),
        _line("学术交流活动经费使用情况说明", 130, 33, _W / 2),
        _line("各有关单位：", 190, 24, 160),
        _line("按照任务分工，相关责任人需要在规定时间内完成材料审核。", 260, 23, 500),
        _line("结合现场检查情况，个别设备存在标签脱落等问题。", 320, 22, 500),
        _line("各单位应明确专人负责资料汇总工作。", 380, 22, 500),
    ]
    st = first_page_structure(lines, _W, _H)
    assert st["has_title"] is True
    assert st["has_recipient"] is True
    assert st["has_header"] is True
    # 标题必须是正文标题，而非文头
    assert st["title_text"] == "学术交流活动经费使用情况说明"


def test_first_page_structure_header_not_chosen_as_title():
    """文头字号大于标题时，标题仍应是文档标题（不是"XX处文件"）。"""
    lines = [
        _line("教务处文件", 60, 40, _W / 2),          # 文头更大
        _line("科研项目阶段性进展情况统计表", 140, 30, _W / 2),
        _line("各有关单位：", 200, 24, 160),
        _line("正文内容行一", 260, 22, 500),
        _line("正文内容行二", 320, 22, 500),
    ]
    st = first_page_structure(lines, _W, _H)
    assert st["title_text"] == "科研项目阶段性进展情况统计表"
    assert st["has_header"] is True


def test_first_page_structure_continuation_table_page():
    """续表页：标题带（续表）+ 顶部无序号标题 → has_continuation 命中。"""
    lines = [
        _line("实验耗材领用管理台账（续表）", 110, 29, _W / 2),
        _line("序号", 170, 28, 120),
        _line("项目内容", 180, 22, 200),
        _line("承办部门", 180, 25, 530),
        _line("资料核验01", 200, 21, 210),
        _line("计划完成", 200, 21, 400),
    ]
    st = first_page_structure(lines, _W, _H)
    assert st["has_continuation"] is True


def test_first_page_structure_section_heading_page():
    """续页：顶部是序号小节标题，无文档标题。"""
    lines = [
        _line("综合事务归档材料/编号：TS-007", 50, 21, 220),
        _line("二、相关情况说明", 130, 27, 200),
        _line("本报告仅供内部测试和工作流程演示，不涉及任何真实组织数据。", 200, 24, 500),
        _line("结合现场检查情况，个别设备存在标签脱落问题。", 260, 23, 500),
        _line("本次检查覆盖人员管理、设备运行、制度落实等环节。", 320, 24, 500),
    ]
    st = first_page_structure(lines, _W, _H)
    assert st["has_section"] is True
    assert st["has_title"] is False


def test_first_page_structure_header_only_is_not_title():
    """续页只重印文头、无文档标题 → has_title=False（关键：防止把文头当标题误判首面）。"""
    lines = [
        _line("后勤保障处文件", 70, 33, _W / 2),          # 仅文头
        _line("二、相关情况说明", 130, 27, 200),           # 序号小节标题
        _line("一、后续工作要求", 160, 24, 190),
        _line("本次检查覆盖人员管理、设备运行、制度落实等环节。", 190, 24, 510),
        _line("质量监测结果表明，常规管理流程总体执行较好。", 250, 24, 520),
        _line("按照任务分工，相关责任人需要完成材料审核。", 310, 20, 370),
    ]
    st = first_page_structure(lines, _W, _H)
    assert st["has_title"] is False, "纯文头页不得被判为有标题"
    assert st["has_header"] is True
    assert st["has_section"] is True


def test_first_page_structure_empty():
    st = first_page_structure([], _W, _H)
    assert st["has_title"] is False and st["has_continuation"] is False
