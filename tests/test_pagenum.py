"""pagenum 纯函数测试：印刷页码提取与页码重置点（不依赖 OCR 引擎）。"""

from scansplit import pagenum


class FakeLine:
    def __init__(self, text, cy, score=0.9):
        self.text = text
        self.y0 = cy - 12
        self.y1 = cy + 12
        self.score = score


H = 1800.0


def test_extract_bottom_page_number():
    lines = [
        FakeLine("2026年10月9日", H * 0.95),   # 落款日期含数字，但整行不匹配页码模式
        FakeLine("教务处", H * 0.93),
        FakeLine("第 3 页", H * 0.98),
    ]
    assert pagenum.extract_page_number(lines, H) == 3


def test_extract_plain_and_dash_formats():
    assert pagenum.extract_page_number([FakeLine("5", H * 0.97)], H) == 5
    assert pagenum.extract_page_number([FakeLine("— 7 —", H * 0.97)], H) == 7
    assert pagenum.extract_page_number([FakeLine("4 / 12", H * 0.97)], H) == 4


def test_extract_header_fallback():
    """页脚没有页码时退回页眉区域。"""
    lines = [FakeLine("2", H * 0.04), FakeLine("正文内容不含页码", H * 0.5)]
    assert pagenum.extract_page_number(lines, H) == 2


def test_extract_none_cases():
    # 落款日期不能被误当页码
    assert pagenum.extract_page_number([FakeLine("2026年10月9日", H * 0.96)], H) is None
    # 正文里的数字不在页脚/页眉区
    assert pagenum.extract_page_number([FakeLine("1. 检查时间：2026年10月。", H * 0.5)], H) is None
    # 空行
    assert pagenum.extract_page_number([], H) is None


def test_reset_points_basic():
    numbers = {1: 1, 2: 2, 3: 1, 4: 2, 5: 3, 6: 1, 7: None, 8: 2}
    assert pagenum.reset_points(numbers) == [3, 6]


def test_reset_points_none_and_edge():
    assert pagenum.reset_points({1: 1, 2: 2, 3: 3}) == []
    assert pagenum.reset_points({1: None, 2: 3, 3: None}) == []
    assert pagenum.reset_points({}) == []
    # 首页无页码，第二页有页码：不构成重置
    assert pagenum.reset_points({1: None, 2: 5}) == []


def test_extract_multi_format_and_monotonic():
    assert pagenum.extract_page_number([FakeLine("第 12 页，共 30 页", H * 0.97)], H) == 12
    assert pagenum.extract_page_number([FakeLine("Page 3 of 5", H * 0.97)], H) == 3
    # 页码连续无重置
    assert pagenum.reset_points({1: 1, 2: 2, 3: 3, 4: 4}) == []
    # 跳页（缺页）不算重置：4 之后是 6
    assert pagenum.reset_points({1: 4, 2: 6}) == []


def test_extract_multi_format_and_monotonic():
    assert pagenum.extract_page_number([FakeLine("第 12 页，共 30 页", H * 0.97)], H) == 12
    assert pagenum.extract_page_number([FakeLine("Page 3 of 5", H * 0.97)], H) == 3
    # 页码连续无重置
    assert pagenum.reset_points({1: 1, 2: 2, 3: 3, 4: 4}) == []
    # 跳页（缺页）不算重置：4 之后是 6
    assert pagenum.reset_points({1: 4, 2: 6}) == []
