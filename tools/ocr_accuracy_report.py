"""OCR 字段级准确率测试：在合成中文公文场景集上统计标题/单位识别准确率。

用法：python tools/ocr_accuracy_report.py
输出：每个场景的识别结果与总体准确率（标题=关键词命中，单位=精确匹配）。
需要本地 OCR 组件与系统中文字体。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from scansplit import ocrfields  # noqa: E402
from scansplit.ocr import OcrEngine  # noqa: E402
from tests import ocrimg  # noqa: E402

# (场景名, 标题关键词(任一命中), 期望单位, 制图参数)
SCENARIOS = [
    ("标准通知", ["教学检查"], "教务处", dict(
        title="关于开展教学检查的通知",
        body_lines=["各学院、各部门：", "为加强教学质量监控，经研究决定，", "本学期开展教学检查工作。"])),
    ("多行标题", ["实验室", "检查报告"], "计算机科学与技术学院", dict(
        title=["2026年度实验室", "安全检查报告"],
        body_lines=["为进一步加强实验室安全管理，", "现将有关工作安排如下。"])),
    ("大字号标题", ["通知"], "校长办公室", dict(
        title="重要通知", body_lines=["各有关单位：", "请按要求执行。"], title_size=88)),
    ("模糊扫描", ["科研工作", "总结"], "科研处", dict(
        title="2026年度科研工作总结",
        body_lines=["一年来，科研工作取得显著进展，", "现将有关情况总结如下。"], blur=1.5)),
    ("倾斜扫描", ["安全"], "保卫处", dict(
        title="关于做好安全工作的通知",
        body_lines=["各单位：", "请认真落实各项安全措施。"], skew_deg=2.5)),
    ("横版表格标题", ["成绩", "统计表"], "教务处", dict(
        title="学生成绩统计表", body_lines=None, landscape=True, table_grid=True)),
    ("正文多单位", ["联合检查"], "科研处", dict(
        title="关于开展联合检查的通知",
        body_lines=["各学院、各部门：", "经教务处、学生处、后勤管理处协商，", "决定开展联合检查。"])),
    ("收文单位排除", ["开学准备"], "学校办公室", dict(
        title="关于做好开学准备工作的通知",
        body_lines=["各学院、各部门：", "现将开学准备工作通知如下。"])),
    ("无落款不编造", ["会议记录"], None, dict(
        title="会议记录",
        body_lines=["时间：2026年10月9日", "地点：会议室", "参加人员：全体成员"])),
]


def _top(cands) -> str:
    return cands[0].text if cands else ""


def main() -> int:
    if not OcrEngine.get().available:
        print("OCR 引擎不可用，无法运行准确率测试。")
        return 2
    if not ocrimg.fonts_available():
        print("系统中文字体不可用，无法运行准确率测试。")
        return 2

    n_title_ok = n_unit_ok = 0
    print(f"{'场景':<12} {'标题识别':<28} {'命中':<4} {'单位识别':<22} {'精确':<4}")
    print("-" * 76)
    for name, title_kws, want_unit, kwargs in SCENARIOS:
        img = ocrimg.make_cn_document(unit=want_unit, **kwargs)
        lines = OcrEngine.get().recognize(img)
        w, h = img.size
        titles = ocrfields.extract_title(lines, w, h)
        first = ocrfields.extract_unit_first_page(lines, w, h)
        last = ocrfields.extract_unit_last_page(lines, w, h)
        merged = ocrfields.merge_unit_candidates(first, last)
        got_title, got_unit = _top(titles), _top(merged)
        t_ok = any(kw in got_title for kw in title_kws)
        u_ok = (got_unit == want_unit) if want_unit else (got_unit == "")
        n_title_ok += t_ok
        n_unit_ok += u_ok
        print(f"{name:<12} {got_title[:26]:<28} {'Y' if t_ok else 'N':<4} "
              f"{got_unit[:20]:<22} {'Y' if u_ok else 'N':<4}")
    n = len(SCENARIOS)
    print("-" * 76)
    print(f"标题准确率（关键词命中）：{n_title_ok}/{n} = {n_title_ok / n:.0%}")
    print(f"单位准确率（精确匹配/无则判空）：{n_unit_ok}/{n} = {n_unit_ok / n:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
