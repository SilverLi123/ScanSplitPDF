"""Read-only product language. Does not analyse, confirm or export documents."""
from dataclasses import dataclass
from . import blankpolicy
from .unit_region import SOURCE as SIGNATURE_SOURCE
import re

COLORS = dict(success="#17643c", warning="#805200", error="#a12828", info="#536173")


@dataclass(frozen=True)
class Issue:
    key: str
    category: str
    label: str
    reason: str
    doc_index: int = 0
    page: int = 1
    group_index: int | None = None
    field: str = ""
    action: str = "page"
    severity: str = "warning"


def candidates(group, field):
    return [c.to_dict() if hasattr(c, "to_dict") else c
            for c in getattr(group, field+"_candidates")]


def field_info(group, field):
    value = getattr(group, field).strip()
    manual = getattr(group, field+"_manual")
    confirmed = getattr(group, field+"_confirmed") and bool(value)
    kind = getattr(group, field+"_edit_kind", "")
    match = next((c for c in candidates(group, field) if c.get("text", "").strip() == value), None) if value else None
    if manual:
        status = {"edited":"人工修改", "confirmed":"人工确认", "candidate":"人工选择候选"}.get(kind, "人工确认/填写（旧记录未区分）")
        source = "人工填写" if kind == "edited" or not match else "原识别来源："+str(match.get("source") or "来源未记录")
    else:
        status = "规则检查通过 · 自动提取" if confirmed else "需要确认 · 自动建议" if value else "需要填写"
        source = str(match.get("source") or "来源未记录") if match else "当前文字无对应 OCR 来源" if value else "未找到可靠来源"
    if match and not (manual and kind == "edited") and match.get("page"):
        source = f"原第 {match['page']} 页 · {source}"
    return status, source, "success" if confirmed else "warning", match


def split_ready(doc, group):
    return (doc.analysis_complete and not doc.analysis_stale and group.boundary_confirmed
            and not doc.pending_for_group(group)
            and not any(p.has_rotation_proposal for p in doc.pages[group.start-1:group.end]))


def name_ready(doc, group):
    return split_ready(doc, group) and not group.need_confirm and bool(group.title.strip() and group.unit.strip())


def state_key(docs):
    """Cheap invalidation for displayed checks; no hashing PDF on each keystroke."""
    return tuple((str(d.path), d.source_sha256, d.analysis_complete, d.analysis_stale,
        d.naming_attempted, tuple(sorted(d.pending_boundaries.items())),tuple(sorted(d.manual_boundaries.items())),tuple(sorted(d.manual_separator_pages)),
        tuple((p.separator,p.omitted_blank,p.split_before,p.effective_rotation,p.rotation_proposal) for p in d.pages),
        tuple((g.start,g.end,g.title,g.unit,g.title_confirmed,g.unit_confirmed,g.boundary_confirmed,
               g.title_manual,g.unit_manual) for g in d.groups)) for d in docs)


def issues(docs):
    result = []
    for di,d in enumerate(docs):
        if not d.analysis_complete:
            result.append(Issue(f"{di}:analyze", "自动分析", "文件尚未分析", "点击顶部“开始自动分析”，生成拆分草稿。", di, action="analyze", severity="info"))
            continue
        for p in d.pages:
            if p.blank and p.blank.verdict in ('maybe', 'blank') and not p.omitted_blank and not p.separator and p.src_index not in d.manual_separator_pages:
                result.append(Issue(f"{di}:blank:{p.src_index}", "空白页", "疑似空白，已保留", "空白证据不足；可能含页码、印章或签名。查看原页后可右键保留或排除。", di,p.src_index,action="page"))
            if p.has_rotation_proposal:
                result.append(Issue(f"{di}:direction:{p.src_index}", "页面方向", "方向需要确认", p.rotation_proposal_note or "请按原页确认文字是否正向；建议尚未应用。", di,p.src_index,action="direction"))
        if d.analysis_stale:
            result.append(Issue(f"{di}:stale", "拆分", "方向改变，需要更新分析", "重新分析保留人工决定；旧自动证据暂不可用于导出。", di,action="reanalyze"))
        for p,reason in sorted(d.pending_boundaries.items()):
            result.append(Issue(f"{di}:boundary:{p}", "拆分交界", "文件归属需要确认", reason,di,p,action="boundary"))
        for gi,g in enumerate(d.groups):
            if not g.boundary_confirmed and not d.pending_for_group(g) and not d.analysis_stale and not any(p.has_rotation_proposal for p in d.pages[g.start-1:g.end]):
                result.append(Issue(f"{di}:group:{gi}", "文件范围", "页码范围需要确认", f"核对原第 {g.start}–{g.end} 页是否属于同一份文件。",di,g.start,gi,action="group"))
            if not d.naming_attempted:
                continue
            for field,label in (("title","标题"),("unit","填报单位")):
                if getattr(g,field).strip() and getattr(g,field+"_confirmed"):
                    continue
                cs = candidates(g,field)
                reason = ("未找到明确的填报单位字段，请按原件填写。" if field=="unit" else "未找到可靠标题，请按原件填写。") if not cs else (
                    "存在多个不同候选，请核对当前文字及来源。" if len({c.get('text') for c in cs})>1 else "自动提取结果需要人工核对。")
                match = field_info(g, field)[3]
                if field == "unit" and match and match.get("source") == SIGNATURE_SOURCE:
                    reason = "末页右下角识别到落款单位，请确认它是否为本文件的填报单位。" + (
                        "存在多个候选，请核对。" if len({c.get('text') for c in cs}) > 1 else "")
                page = (match or {}).get("page") or g.start
                result.append(Issue(f"{di}:{field}:{gi}",label,label+"需要确认",reason,di,page,gi,field,"field"))
    return result


def step_counts(docs, exported=0):
    groups=[(d,g) for d in docs for g in d.groups]
    return [(len(docs),len(docs),"文件"),
        (sum(d.analysis_complete and not d.analysis_stale for d in docs),len(docs),"文件"),
        (sum(split_ready(d,g) for d,g in groups),len(groups),"份"),
        (sum(name_ready(d,g) for d,g in groups),len(groups),"份"),
        (exported,len(groups),"份")]


def page_info(doc, number):
    p=doc.pages[number-1]
    gi=next((i for i,g in enumerate(doc.groups) if g.start<=number<=g.end),None)
    belongs = f"文件 {gi+1}" if gi is not None else "未归入文件"
    if p.omitted_blank:
        belongs = "不进入输出" + (f" · 原属文件 {gi+1}" if gi is not None else "")
    if p.omitted_blank:
        page_type=("已人工确认删除（仅输出，可恢复）" if number in doc.manual_separator_pages else
                   "明确空白 · 规则自动删除（仅输出，可恢复）" if blankpolicy.valid_exclusion(p) else "空白排除证据无效 · 阻止导出")
    elif p.separator:
        page_type="已人工确认排除" if number in doc.manual_separator_pages else "排除状态需核查"
    elif not p.blank:
        page_type="尚未分析 · 保留"
    elif p.blank.verdict=="maybe":
        page_type="疑似空白 · 保留"
    elif p.blank.verdict=="blank":
        page_type="规则判断内部空白 · 保留" if p.blank_context=="internal_blank" else "空白候选 · 保留"
    else:
        page_type="内容页 · 保留"
    rotation=(f"原件方向 {p.original_rotation}°；" if p.original_rotation else "")
    rotation += (f"人工调整 {p.effective_rotation}°（已生效）" if p.is_rotation_manual else
                 f"自动检测调整 {p.effective_rotation}°（已生效）" if p.effective_rotation else "保持原件方向")
    if not p.is_rotation_manual and not p.has_rotation_proposal and p.rotation_proposal_note.startswith("规则检查通过"):
        rotation += "（四方向规则检查通过）"
    if p.has_rotation_proposal:
        rotation+=f"；建议调整 {p.rotation_proposal}°（未应用）"
    boundary = ""
    if number in doc.pending_boundaries:
        boundary="该页是否另起一份，需要确认："+doc.pending_boundaries[number]
    elif p.split_before:
        boundary="人工确认新文件起点" if doc.manual_boundaries.get(number) is True else "规则自动建立的新文件起点"
        if number not in doc.manual_boundaries and number in doc.boundary_evidence:
            boundary += "："+doc.boundary_evidence[number]["reason"]
    if number+1 in doc.pending_boundaries:
        boundary+=("\n" if boundary else "")+"下一页可能开始新文件，需要确认。"
    return f"{belongs} · 原第 {number} 页\n{page_type}",rotation,boundary


def precheck_issues(docs, messages, prefix="precheck"):
    out=[]
    for i,msg in enumerate(messages):
        di=next((j for j,d in enumerate(docs) if d.name in msg),0)
        page=re.search(r"第\s*(\d+)\s*(?:页|[-–])",msg)
        p=int(page.group(1)) if page else 1
        p=max(1,min(p,docs[di].page_count)) if docs else 1
        out.append(Issue(f"{prefix}:{i}","导出阻断","无法导出",msg,di,p,action="page",severity="error"))
    return out


def export_feedback(docs, selected, report, canceled=False):
    """Display only completed checks, including skipped/canceled export cases."""
    ok,failed=len(report.succeeded),len(report.failed)
    canceled=bool(canceled or report.canceled)
    paths={str(docs[di].path) for di,gi in selected}
    source_done=bool(paths) and all(k in report.src_sha_before and k in report.src_sha_after for k in paths)
    source_ok=source_done and all(report.src_sha_before[k]==report.src_sha_after[k] for k in paths)
    completed=(not canceled and len(report.results)==len(selected) and not failed and ok==len(selected))
    coverage_ok=completed and source_ok and not report.coverage_issues
    lines=[f"{'导出已取消' if canceled else '导出结束'}：已验证成功 {ok} 份，失败 {failed} 份，未完成 {max(0,len(selected)-len(report.results))} 份。",
           "源文件检查："+("已处理源文件 SHA-256 一致" if source_ok else "未全部完成" if not source_done else "失败，源文件发生变化"),
           "页面完整性："+("所选文件覆盖率校验通过" if coverage_ok else "失败，请查看问题详情" if report.coverage_issues else "未全部完成")]
    errors=[m for values in report.coverage_issues.values() for m in values]
    errors += [f"{r.doc_name}：{r.error}" for r in report.failed]
    if source_done and not source_ok:
        errors.append("源文件 SHA-256 不一致，请停止使用本次导出结果并核对源文件。")
    if report.manifest_error:
        errors.append("页面去向记录写入失败："+report.manifest_error)
    if report.manifest_path:
        lines.append("页面去向记录："+str(report.manifest_path))
    return dict(ok=ok,complete=coverage_ok and not report.manifest_error,lines=lines,errors=errors)
