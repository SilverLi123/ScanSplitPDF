"""Blank-run + printed-pagination decisions. No page exclusion or PDF writes."""
import re
import math
from dataclasses import replace


def form_identity(lines, width, height, structure, units):
    """Read a form's identity from actual header boxes, not naming scores.

    Naming confidence includes format priors. Those priors must not discard a
    clearly read, repeated form header as independent pagination evidence.
    This is only a sequence cross-check; it never approves a title or unit name.
    Require one complete title line and one explicit reporting-unit label/value,
    with every supporting OCR box strong. No footer/header organisation fallback.
    """
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0
            and structure.get('has_title') and structure.get('title_is_max')
            and not structure.get('has_section') and not structure.get('has_continuation')
            and len(units) == 1 and units[0].source == '填报单位'):
        return None
    title = structure.get('title_text', '')
    ts = [ln for ln in lines if ln.text.strip() == title]
    if len(ts) != 1 or len(title) < 4:
        return None
    def strong(ln):
        return (math.isfinite(ln.score) and .85 <= ln.score <= 1
                and all(map(math.isfinite, (ln.x0, ln.y0, ln.x1, ln.y1)))
                and 0 <= ln.x0 < ln.x1 <= width and 0 <= ln.y0 < ln.y1 <= height)
    t = ts[0]
    if not (strong(t) and t.cy <= height*.20 and t.height >= height*.02
            and abs(t.cx-width/2) < width*.22):
        return None
    box = units[0].bbox
    if not (box and len(box) == 4 and all(map(math.isfinite, box))):
        return None
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height and y1 <= height*.22):
        return None
    supporting = [ln for ln in lines if ln.x0 >= x0-1 and ln.x1 <= x1+1
                  and ln.y0 >= y0-1 and ln.y1 <= y1+1]
    label = re.compile(r'^填\s*报\s*单\s*位\s*[：:]?\s*(.*)$')
    labels = [ln for ln in supporting if label.match(ln.text.strip())]
    if (len(labels) != 1 or not 1 <= len(supporting) <= 2
            or not all(strong(ln) for ln in supporting)):
        return None
    value = label.match(labels[0].text.strip()).group(1).strip()
    if value:
        if len(supporting) != 1 or value != units[0].text:
            return None
    elif len(supporting) != 2 or not any(
            ln is not labels[0] and ln.text.strip() == units[0].text for ln in supporting):
        return None
    return title, units[0].text


def confirm_pagination_sequences(features, content, manual, separators):
    """Certify observed form labels with independent sequence and identity evidence.

    Single weak OCR, bare digits, duplicate/conflicting candidates and missing
    numbers never qualify. Require three actual consecutive numbers at the same
    position and independent identity evidence. Form labels need a strong title
    and labelled reporting unit; decorated footer numbers need a strong repeated
    document title on all three pages and no conflicting labelled units.
    Manual boundaries and separators stop a sequence; no missing digit is filled.
    """
    approved = set()
    for i in range(len(content)-2):
        pages = content[i:i+3]
        if any(p in manual for p in pages[1:]) or any(
                p in separators for p in range(pages[0]+1, pages[-1])):
            continue
        fs = [features.get(p, {}) for p in pages]
        es = [f.get('pagination') for f in fs]
        if not all(e and e.number is not None and e.position and not e.ambiguous
                   and math.isfinite(e.score) and all(map(math.isfinite,e.position)) for e in es):
            continue
        form = all(e.kind == 'form_label' and e.score >= .70 for e in es)
        footer = all(e.kind == 'decorated' and e.score >= .50
                     and not 1900 <= e.number <= 2099
                     and e.position[1] >= .94 and abs(e.position[0]-.5) <= .18 for e in es)
        if not (form or footer):
            continue
        if [e.number for e in es] != list(range(es[0].number, es[0].number+3)):
            continue
        if len({e.total for e in es}) != 1 or any(
                abs(e.position[0]-es[0].position[0]) > .025
                or abs(e.position[1]-es[0].position[1]) > .015 for e in es):
            continue
        identities = [f.get('reporting_identity' if form else 'running_title') for f in fs]
        if not all(identities) or len(set(identities)) != 1:
            continue
        if footer and (any(len(f.get('units',set())) > 1 for f in fs)
                       or len(set().union(*(f.get('units',set()) for f in fs))) > 1):
            continue
        if any(f.get('structure', {}).get('has_continuation') for f in fs):
            continue
        approved.update(pages)
    for p in approved:
        e = features[p]['pagination']
        if not e.reliable:
            features[p]['pagination'] = replace(e, reliable=True,
                reason=('三页明确页次标签连续，位置、标题及填报单位一致（规则交叉核对）'
                        if e.kind == 'form_label' else
                        '三页页边印刷页码连续，位置与同源标题一致（规则交叉核对）'))
    return approved


def blank_start(previous, current, verified_blank):
    """A verified gap plus distinct reliable first-page fields can stand alone.

    Never override printed-number conflicts or continuation/appendix evidence.
    """
    if not verified_blank or not current.get("reliable_first_page"):
        return None
    before, after = previous["pagination"], current["pagination"]
    if before.text or after.text or before.number is not None or after.number is not None:
        return None
    st, old = current["structure"], previous["structure"]
    if st.get("has_continuation") or re.match(r"^(附件|附录|附表)[：:\s\d一二三四]", st.get("title_text", "")):
        return None
    from .splitctx import _titles_same_source
    units, previous_units = current.get("units", set()), previous.get("units", set())
    changed_unit = len(units) == len(previous_units) == 1 and units != previous_units
    changed_title = bool(old.get("title_text") and st.get("title_text")
        and not _titles_same_source(old["title_text"], st["title_text"]))
    if not (changed_unit or changed_title):
        return None
    return "split", "明确空白间隔与可靠首页标题、填报单位相符，且前后信息发生变化；从后页拆分，空白从输出删除"


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
    if any('规则交叉核对' in e.reason for e in (before, after)):
        comparison += '多页页码与文档信息已交叉核对；'
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


def check_sequence(features, content, index, manual, separators, decision, reason):
    """Neighbouring evidence can veto a reset, never fabricate missing numbers."""
    window = content[max(0,index-2):index+2]
    sequence = [dict(page=p, **features[p]["pagination"].to_dict()) for p in window if p in features]
    if decision != "split":
        return decision, reason, sequence
    previous, current = content[index-1], content[index]
    def connected(a, b):
        return not manual.get(b) and not any(p in separators for p in range(a+1,b))
    before = features[previous]["pagination"]
    after = features[current]["pagination"]
    if before.number is None or after.number is None:
        return decision, reason, sequence  # Independently verified blank/first-page path.
    # A cropped table cell can look like a centred footer digit. Require a full
    # surrounding number sequence for bare/fraction/dash formats, no extra title
    # or blank requirement. Explicit Page/第...页 labels still stand alone.
    bare = lambda evidence: bool(evidence.text) and not re.match(r"^(第|page\s*\d|页\s*(?:次|码)\s*[：:])", evidence.text, re.I)
    if bare(before) or bare(after):
        pp = content[index-2] if index >= 2 else None
        np = content[index+1] if index+1 < len(content) else None
        earlier = features.get(pp,{}).get("pagination")
        later = features.get(np,{}).get("pagination")
        if not (earlier and later and earlier.reliable and later.reliable
                and earlier.number == before.number-1 and later.number == 2
                and connected(pp,previous) and connected(current,np)):
            return "review", reason+"；页边数字缺少前后连续序列，不能确定是否印刷页码", sequence
    if index >= 2:
        pp = content[index-2]
        earlier = features.get(pp,{}).get("pagination")
        if (earlier and earlier.reliable and connected(pp,previous) and before.number > 1
                and earlier.number != before.number-1):
            return "review", reason+"；此前页码序列不连续，不能确定前份末页", sequence
    if index+1 < len(content):
        np = content[index+1]
        later = features.get(np,{}).get("pagination")
        if later and later.reliable and connected(current,np):
            if later.number not in (1,2) or (later.number == 2 and after.total and later.total and after.total != later.total):
                return "review", reason+"；后续页码未连续或总页数矛盾，请核对", sequence
    return decision, reason, sequence
