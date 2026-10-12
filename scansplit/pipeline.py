"""Offline analysis: detect -> automatic orientation -> boundaries. No PDF writes."""
from __future__ import annotations

import re
from . import autoname, blankdetect, blankpolicy, grouping, ocrfields, pagenum, splitctx, jointsplit, pagination_read, automation
from .ocr import OcrEngine
from .render import PageRenderer


def boundary_decision(previous: dict, current: dict) -> tuple[str, str]:
    """Return split / keep / review. Contradictory evidence never auto-splits."""
    st = current['structure']
    title = st.get('title_text', '')
    pn, prev_pn = current.get('number'), previous.get('number')
    same = splitctx._titles_same_source(title, previous['structure'].get('title_text', ''))
    strong = st.get('has_title') and (st.get('title_is_max') or st.get('has_recipient'))
    units, prev_units = current.get('units', set()), previous.get('units', set())
    unit_change = bool(units and prev_units and units != prev_units)
    reset = pn == 1 or (pn is not None and prev_pn is not None and pn < prev_pn)
    if st.get('has_continuation') or re.match(r'^(附件|附录|附表)[：:\s\d一二三四]', title):
        if unit_change:
            return 'review', '续表/附件标记与填报单位变化冲突'
        return 'keep', '续表或附件默认随原文档保留'
    if same and not unit_change:
        if reset:
            return 'review', '同名表格出现页码重置，可能是续页或另一份填报'
        if pn is not None and prev_pn is not None and pn == prev_pn + 1:
            return 'keep', '同源标题且页码连续'
        return 'review', '同名表格缺少连续页码，可能是另一份填报'
    if strong and reset:
        return 'split', '首页标题结构与页码重置一致'
    if strong or reset or unit_change:
        return 'review', '出现首页/页码/填报单位变化，证据不足，请确认'
    if pn is not None and prev_pn is not None and pn == prev_pn + 1:
        return 'keep', '页码连续且无冲突证据'
    return 'review', '未获得连续归属或新文档的可靠证据，请核对'


def update_boundary_state(doc):
    for g in doc.groups:
        if doc.analysis_complete:
            g.boundary_confirmed = not bool(doc.pending_for_group(g))
        else:
            # One manual split does not certify every other page in the new ranges.
            g.boundary_confirmed = all(p in doc.manual_boundaries for p in g.pages)
        if any(p.has_rotation_proposal or p.direction_review_required for p in doc.pages[g.start - 1:g.end]):
            g.boundary_confirmed = False


def analyze_document(doc, params=None, engine=None, progress=None, cancel=None, remove_blank=False):
    """Single worker-owned renderer; cache analysed OCR for all downstream stages."""
    engine = engine or OcrEngine.get()
    params = params or blankdetect.DetectParams()
    progress = progress or (lambda *a: None)
    cancel = cancel or (lambda: False)
    renderer = PageRenderer(doc.path)
    try:
        results = blankdetect.detect_document(renderer, doc.page_count, params,
            lambda i: False, progress_cb=lambda i,n: progress(i,n,'检测页面'), cancel_cb=cancel)
        if cancel():
            return
        for ps, result in zip(doc.pages, results):
            ps.blank = result
            if ps.src_index not in doc.manual_boundaries:
                ps.split_before = False
        doc.boundary_evidence = {}
        if remove_blank:
            blankpolicy.apply(doc, renderer, cancel, progress)
        else:
            for ps in doc.pages:
                if ps.src_index not in doc.manual_separator_pages:
                    ps.omitted_blank = False
                    ps.blank_exclusion_evidence = {}
        if cancel():
            return
        content = [ps.src_index for ps in doc.pages if ps.blank.verdict != 'blank'
                   and not (ps.src_index in doc.manual_separator_pages and (ps.separator or ps.omitted_blank))]
        if not engine.available:
            doc.pending_boundaries = {p.src_index: '本地 OCR 不可用，请人工核对该页方向与文档归属'
                                      for p in doc.pages}
            for ps in doc.pages:
                if ps.src_index in content and not ps.is_rotation_manual:
                    ps.rotation_proposal = 0
                    ps.rotation_proposal_confidence = 0
                    ps.rotation_proposal_note = '未获得有效方向建议：本地 OCR 不可用，请检查并确认当前方向'
            grouping.auto_group(doc)
            for g in doc.groups:
                g.boundary_confirmed = False
            doc.analysis_complete = True
            doc.analysis_stale = False
            doc.naming_attempted = False
            return
        directions = autoname.analyze_directions(doc, renderer,
            [p for p in content if not doc.pages[p-1].is_rotation_manual],
            progress_cb=lambda i,n: progress(i,n,'检查方向'), cancel_cb=cancel, fast=True)
        if cancel():
            return
        by_page = {r.page:r for r in directions}
        for p in content:
            ps = doc.pages[p-1]
            if ps.is_rotation_manual:
                continue
            r = by_page.get(p) or autoname.DirectionResult(
                page=p, qualities={}, suggestion_note="未得到方向分析结果，请人工检查")
            changed = automation.apply_direction_result(ps, r)
            if changed:
                for g in doc.groups:
                    if g.start <= p <= g.end:
                        if not g.title_manual:
                            g.title_confirmed = False
                        if not g.unit_manual:
                            g.unit_confirmed = False
        cache, features = {}, {}
        for done, p in enumerate(content, 1):
            if cancel():
                return
            ps = doc.pages[p-1]
            if ps.has_rotation_proposal:
                cache[p] = ([], 1, 1)
                continue
            lines, w, h, _ = autoname._ocr_page_rotation_aware(renderer, p-1, engine, 1200, ps)
            cache[p] = (lines, w, h)
            evidence = pagination_read.refine(renderer,p-1,engine,ps.effective_rotation,
                pagenum.pagination_evidence(lines,w,h),cancel)
            structure = ocrfields.first_page_structure(lines,w,h)
            titles = ocrfields.extract_title(lines,w,h)
            units = ocrfields.extract_reporting_unit(lines,w,h)
            features[p] = dict(structure=structure,
                reliable_first_page=bool(structure.get('has_title') and structure.get('title_is_max')
                    and not structure.get('has_section') and not structure.get('has_continuation')
                    and titles and titles[0].confidence >= .85
                    and ocrfields._title_pattern_score(titles[0].text) >= 1 and units
                    and units[0].confidence >= .85 and len({c.text for c in units}) == 1),
                number=pagenum.extract_page_number(lines,h),
                pagination=evidence,
                units={c.text for c in units})
            progress(done, len(content), '分析文档边界')
        evs = splitctx.judge_blank_context(doc, renderer, engine, page_cache=cache, cancel_cb=cancel)
        for ev in evs:
            if ev.verdict == splitctx.SEPARATOR:
                ev.verdict = splitctx.UNCERTAIN
                ev.reason = '建议为分隔空白；页面默认保留，请确认拆分及是否排除空白'
        splitctx.apply_context(doc, evs)
        doc.pending_boundaries = {}
        for ev in evs:
            if ev.verdict == splitctx.UNCERTAIN:
                if blankpolicy.valid_exclusion(doc.pages[ev.page-1]) and (
                        ev.prev_content_page is None or ev.next_content_page is None):
                    continue  # Leading/trailing verified blanks are not boundaries.
                p = ev.next_content_page or ev.page
                if p not in doc.manual_boundaries and ev.page not in doc.manual_separator_pages:
                    doc.pending_boundaries[p] = ev.reason
        for i, p in enumerate(content):
            if i == 0:
                continue
            prev = content[i-1]
            if p in doc.manual_boundaries:
                doc.pages[p-1].split_before = doc.manual_boundaries[p]
                continue
            if any(doc.pages[j-1].separator for j in range(prev+1,p)):
                continue
            if p not in features or prev not in features:
                doc.pending_boundaries[p] = '相邻页方向待确认，确认后重新自动拆分'
                continue
            decision, reason = boundary_decision(features[prev], features[p])
            blank_pages = list(range(prev+1,p))
            numbered = jointsplit.decide(features[prev], features[p], blank_pages)
            if numbered is not None:
                decision, reason = numbered
            elif decision == 'split':
                decision, reason = 'review', '首页与数字变化提示新文件，但印刷页码证据不足，请确认'
            blank_start = jointsplit.blank_start(features[prev], features[p],
                bool(blank_pages) and all(blankpolicy.valid_exclusion(doc.pages[bp-1]) for bp in blank_pages))
            if blank_start is not None:
                decision, reason = blank_start
            decision, reason, sequence = jointsplit.check_sequence(features, content, i,
                doc.manual_boundaries, doc.separators, decision, reason)
            if blank_pages:
                # Document membership and output omission are independent decisions.
                for bp in blank_pages:
                    ps = doc.pages[bp-1]
                    ps.blank_context = "internal_blank" if decision == "keep" else "uncertain"
                    if bp not in doc.manual_separator_pages:
                        ps.separator = False
                doc.pending_boundaries.pop(p, None)
            removed = [bp for bp in blank_pages if doc.pages[bp-1].omitted_blank]
            if removed:
                reason = reason.replace('空白候选（保留）', '明确空白').replace(
                    '页码连续，作为内部空白随原文件保留', '页码连续，仍属于同一份文件')
                reason += f'；原第 {removed} 页从输出删除，删除不额外拆分'
            doc.boundary_evidence[p] = dict(previous_page=prev, next_page=p,
                blank_pages=blank_pages, previous_pagination=features[prev]['pagination'].to_dict(),
                next_pagination=features[p]['pagination'].to_dict(), sequence=sequence,decision=decision, reason=reason)
            doc.pages[p-1].split_before = decision == 'split'
            if decision == 'review':
                doc.pending_boundaries[p] = reason
        # Unreadable content is never silently treated as a reliable group.
        for p in content:
            if not cache.get(p, ([],))[0] and not doc.pages[p-1].has_rotation_proposal:
                if p not in doc.manual_boundaries:
                    doc.pending_boundaries[p] = '未识别到可靠文字，请核对该页的文档归属'
        grouping.auto_group(doc)
        doc.analysis_complete = True
        doc.analysis_stale = False
        doc.naming_attempted = False
        update_boundary_state(doc)
        doc.status = 'grouped'
    finally:
        renderer.close()


def organize_document(doc, params=None, engine=None, progress=None, cancel=None, remove_blank=True):
    """One worker transaction: analysis, then naming where boundaries are reliable."""
    engine = engine or OcrEngine.get()
    cancel = cancel or (lambda: False)
    snapshot = doc.snapshot()
    try:
        analyze_document(doc, params, engine, progress, cancel, remove_blank=remove_blank)
        if not cancel():
            automation.name_ready(doc, engine, progress, cancel)
        if cancel():
            doc.restore(snapshot)
            return False
        return True
    except Exception:
        doc.restore(snapshot)
        raise
