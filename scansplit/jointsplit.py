"""Blank-run + printed-pagination decisions. No page exclusion or PDF writes."""
import re


def decide(previous, current, blank_pages=()):
    """A reliable restart can stand alone; a blank run corroborates it, never deletes.

    Return None for no-gap cases without reliable pagination, letting existing
    continuation/title rules provide suggestions, never silently accepting weak resets.
    """
    before, after = previous["pagination"], current["pagination"]
    blank_text = "、".join(map(str, blank_pages))
    prefix = f"原第 {blank_text} 页为空白候选（保留）；" if blank_pages else "无空白间隔；"
    st = current["structure"]
    units, old_units = current.get("units", set()), previous.get("units", set())
    continuation = st.get("has_continuation") or re.match(r"^(附件|附录|附表)[：:\s\d一二三四]", st.get("title_text", ""))
    if len(units) > 1 or len(old_units) > 1:
        return "review", prefix+"填报单位候选冲突，请确认文件归属"
    if not (before.reliable and after.reliable):
        has_candidate = before.number is not None or after.number is not None or before.text or after.text
        return ("review", prefix+f"两侧页码不能可靠比较：前页{before.reason}；后页{after.reason}") if blank_pages or has_candidate else None
    a, b = before.number, after.number
    comparison = f"两侧印刷页码 {a} → {b}；"
    if b == 1:
        if continuation:
            return "review", prefix+comparison+"页码重新从1开始，但存在续表/附件标记"
        if before.total is not None and a != before.total:
            return "review", prefix+comparison+f"前份标注共{before.total}页但尚未到末页，请核对"
        if a == 1 and not blank_pages and before.total != 1:
            return "review", prefix+comparison+"连续出现第1页，不能确定是否另一份文件"
        return "split", prefix+comparison+("空白间隔与页码重新从1开始一致，从后页拆分" if blank_pages else "可靠页码重新从1开始，从后页拆分")
    if units and old_units and units != old_units:
        return "review", prefix+comparison+"页码关系与填报单位变化需要核对"
    if before.total and after.total and before.total != after.total:
        return "review", prefix+comparison+"两侧总页数不同，请核对"
    if before.total is not None and b > before.total:
        return "review", prefix+comparison+"后页超过前份标注的总页数"
    if b == a + 1:
        # A clear new title is contradictory evidence even with continuous numbers.
        pst = previous["structure"]
        from .splitctx import _titles_same_source
        if (st.get("has_title") and (st.get("title_is_max") or st.get("has_recipient"))
                and pst.get("title_text") and st.get("title_text") and not continuation
                and not _titles_same_source(pst["title_text"], st["title_text"])):
            return "review", prefix+comparison+"页码连续但首页标题变化，请确认"
        return "keep", prefix+comparison+("页码连续，作为内部空白随原文件保留" if blank_pages else "页码连续，随原文件保留")
    return "review", prefix+comparison+"页码跳跃或回退且未明确重新从1开始，请确认"
