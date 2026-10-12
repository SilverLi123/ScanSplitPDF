"""Read-only next-step guidance. Never certifies OCR, boundaries or export."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NextStep:
    stage: int
    action: str
    title: str
    detail: str
    button: str
    doc_index: int = 0


def next_step(docs):
    if not docs:
        return NextStep(0, "open", "导入需要整理的扫描 PDF", "可多选或直接拖入窗口。源文件始终保留。", "选择 PDF…")
    for i, doc in enumerate(docs):
        if not doc.analysis_complete:
            return NextStep(1, "analyze", "文件已导入，开始自动分析", "在本机检查空白、方向与文件交界，生成拆分草稿；不确定的结果交给你确认。", "开始自动分析", i)
    for i, doc in enumerate(docs):
        count = sum(p.has_rotation_proposal for p in doc.pages)
        if count:
            return NextStep(2, "review", f"{doc.name}：{count} 页方向需要检查", "在检查窗口把页面转正并确认；结束后系统自动更新拆分。", "检查页面方向", i)
        if any(p.direction_review_required for p in doc.pages):
            return NextStep(2, "review", f"{doc.name}：自动回正结果需要复查", "方向建议已应用；翻页审核后可一次确认本批方向，只纠正错误页。", "复查自动回正", i)
        if getattr(doc, "analysis_stale", False):
            return NextStep(2, "reanalyze", "方向已确认，更新拆分结果", "系统会按已确认的方向重新分析，保留你的人工决定。", "更新拆分并继续检查", i)
    for i, doc in enumerate(docs):
        if not doc.groups:
            return NextStep(2, "review", f"{doc.name}：没有可导出的内容页", "查看已删除空白页；若需保留，右键缩略图选择“恢复选中页”。", "查看空白页处理", i)
        if doc.pending_boundaries or any(not g.boundary_confirmed for g in doc.groups):
            return NextStep(2, "review", f"{doc.name}：拆分需要检查", "并排查看前后页，判断“这里开始新文件”还是“属于上一份”。", "检查拆分结果", i)
    for i, doc in enumerate(docs):
        if not getattr(doc, "naming_attempted", False) and any(
            not g.title_confirmed or not g.unit_confirmed or not g.title.strip() or not g.unit.strip()
            for g in doc.groups
        ):
            return NextStep(3, "name", "拆分已确认，可以识别文件名", "只提取标题和明确标注的填报单位，找不到的内容由你补填。", "识别标题与填报单位", i)
    for i, doc in enumerate(docs):
        if any(g.need_confirm or not g.title.strip() or not g.unit.strip() for g in doc.groups):
            return NextStep(3, "names", f"{doc.name}：确认文件名称", "在文件名称卡片核对标题、填报单位及来源；需要改字时点“修改”。", "定位待确认名称", i)
    return NextStep(4, "export", "已准备好导出", "检查导出清单、页码范围及排除页，再选择保存位置。", "查看清单并导出…")
