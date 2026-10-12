"""批量导出：pypdf 复制页面对象（保真）、写入 /Rotate、命名、冲突保护、
进度/日志/取消、每份页码核对、输出后自动保真校验。源文件只读不改。"""

from __future__ import annotations

import os
import uuid
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from . import verify, blankpolicy
from .model import Group, ScanDocument
from .naming import DEFAULT_TEMPLATE, build_filename, unique_path
from .rotation import apply_target_rotation


@dataclass
class ExportOptions:
    template: str = DEFAULT_TEMPLATE
    out_dir: str | Path = ""
    on_log: object = None  # callable(str)
    on_progress: object = None  # callable(done, total, phase)
    cancel_check: object = None  # callable() -> bool
    # 保留旧调用签名；正式导出始终执行字段与完整性检查，不能放宽。
    strict: bool = True
    # v1.9.1 改进3：按清单导出时指定要导出的 (doc_index, group_index) 集合。
    # None = 全部导出（默认）。指定后覆盖率校验只针对被选中的组，
    # 未勾选的组视为"用户主动跳过"，不计入漏页。
    only: object = None  # set[tuple[int, int]] | None


@dataclass
class ExportResult:
    group: Group
    doc_name: str
    filename: str = ""
    path: Path | None = None
    pages: list = field(default_factory=list)  # 实际导出的源页码（1 起始）
    ok: bool = False
    error: str = ""


@dataclass
class ExportReport:
    results: list[ExportResult] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    src_sha_before: dict = field(default_factory=dict)  # path -> sha256
    src_sha_after: dict = field(default_factory=dict)
    coverage_issues: dict = field(default_factory=dict)  # doc_name -> [问题]
    canceled: bool = False
    manifest_path: Path | None = None
    manifest_error: str = ""

    @property
    def succeeded(self) -> list[ExportResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[ExportResult]:
        return [r for r in self.results if not r.ok]


def _log(report: ExportReport, opts: ExportOptions, msg: str) -> None:
    report.logs.append(msg)
    if opts.on_log:
        opts.on_log(msg)


def preflight(docs: list[ScanDocument], opts: ExportOptions) -> list[str]:
    """导出前完整检查，返回问题列表（空列表 = 可以导出）。"""
    issues: list[str] = []
    template = opts.template or DEFAULT_TEMPLATE
    needs_title = "{title}" in template
    needs_unit = "{unit}" in template

    out_dir = Path(opts.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return [f"无法创建输出目录 {out_dir}：{e}"]
    probe = out_dir / f".~write_test_{uuid.uuid4().hex[:6]}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        return [f"输出目录不可写 {out_dir}：{e}"]

    for doc in docs:
        for gi, g in enumerate(doc.groups):
            if needs_title and not g.title.strip():
                issues.append(f"{doc.name} 文档{gi + 1}（第{g.start}-{g.end}页）：标题为空")
            if needs_unit and not g.unit.strip():
                issues.append(f"{doc.name} 文档{gi + 1}（第{g.start}-{g.end}页）：单位为空白")
            if g.need_confirm:
                issues.append(
                    f"{doc.name} 文档{gi + 1}（第{g.start}-{g.end}页）："
                    "存在待确认字段（边界/标题/填报单位），阻止导出"
                )
            if g.start < 1 or g.end > doc.page_count or g.start > g.end:
                issues.append(f"{doc.name} 文档{gi + 1}：页码范围无效（{g.start}-{g.end}）")
        # 业务完整性：每个原页必须"已导出"或"经用户确认排除"（禁止静默丢页）
        issues.extend(business_integrity_issues(doc))
    return issues


def business_integrity_issues(doc: ScanDocument) -> list[str]:
    """业务完整性核对（v1.9 阶段 A0）：每个原始页必须被明确归类为
    "已导出"（在某个分组内）或"经用户确认排除"（人工标记的分隔页）。

    规则（保守，fail-closed）：
    - 位于分组内的页 → 已导出，正常；
    - 未在任何分组内的页（= 当前被标为分隔页而排除的页）：
      * 若该页**被用户人工确认过**（src_index ∈ doc.manual_separator_pages）
        或**上下文判定为 separator** → 视为"经确认排除"，允许；
      * 新自动空白排除必须有有效整页含页边检查及无文本层证据；不因删除而拆分；
      * 否则（uncertain / 无上下文判定 / 视觉空白但未确认）→ **禁止静默排除**，
        列为问题，阻断导出。
    返回问题字符串列表（空 = 通过）。
    """
    counts: dict[int, int] = {}
    issues: list[str] = []
    for g in doc.groups:
        if not (1 <= g.start <= g.end <= doc.page_count):
            issues.append(f"{doc.name}：页码范围无效 {g.start}-{g.end}")
            continue
        for p in g.pages:
            counts[p] = counts.get(p, 0) + 1
        if not g.pages:
            issues.append(f"{doc.name}：分组没有可导出的页面 {g.start}-{g.end}")
    duplicates = [p for p, n in counts.items() if n > 1]
    if duplicates:
        issues.append(f"{doc.name}：分组重复包含原页 {duplicates}")
    in_group = set(counts)
    manual = getattr(doc, "manual_separator_pages", None) or set()
    for st in doc.pages:
        if st.src_index in in_group:
            if st.separator or st.omitted_blank:
                issues.append(f"{doc.name} 第 {st.src_index} 页同时属于分组与排除页")
            continue  # 已导出
        # 未在分组内 → 被排除。必须有明确、经确认的排除依据。
        confirmed = ((st.separator or st.omitted_blank) and st.src_index in manual) or (st.separator and (
            st.src_index in manual
            or getattr(st, "blank_context", "") == "separator"
        )) or blankpolicy.valid_exclusion(st)
        if not confirmed:
            ctx = getattr(st, "blank_context", "") or "未判定"
            issues.append(
                f"{doc.name} 第 {st.src_index} 页未被导出，且没有经确认的排除依据"
                f"（业务属性：{ctx}）——禁止静默丢页"
            )
    return issues


def integrity_preflight(docs: list[ScanDocument], opts: ExportOptions) -> list[str]:
    """Hard contract enforced for UI, direct API and partial exports alike."""
    issues = []
    for di, doc in enumerate(docs):
        issues.extend(business_integrity_issues(doc))
        try:
            if verify.file_sha256(doc.path) != doc.source_sha256:
                issues.append(f"{doc.name}：源文件自导入后发生变化，请重新导入")
        except OSError as exc:
            issues.append(f"{doc.name}：无法读取源文件：{exc}")
        for gi, g in enumerate(doc.groups):
            if opts.only is not None and (di, gi) not in opts.only:
                continue
            if not g.boundary_confirmed or doc.pending_for_group(g):
                issues.append(f"{doc.name} 第 {g.start}-{g.end} 页：拆分边界待确认")
            if any(p.has_rotation_proposal for p in doc.pages[g.start-1:g.end]):
                issues.append(f"{doc.name} 第 {g.start}-{g.end} 页：方向建议待处理")
    if opts.only is not None and any(
        not (0 <= di < len(docs) and 0 <= gi < len(docs[di].groups))
        for di, gi in opts.only
    ):
        issues.append("导出选择包含无效分组")
    return issues


def coverage_issues(doc: ScanDocument, results: list[ExportResult],
                    only_groups: set | None = None) -> list[str]:
    """全局页面覆盖率校验（v1.8）：每个有效原页（组内页）必须恰好导出一次。

    漏导、重复导出、导出了组外页均视为问题。实际 Group.pages 只允许移除
    已确认的排除页，business_integrity_issues 独立验证排除依据。

    only_groups（v1.9.1 改进3）：按清单导出时，只针对这些组索引校验覆盖率；
    未勾选的组属用户主动跳过，不计入漏页。
    """
    if only_groups is None:
        expected = sorted(p for g in doc.groups for p in g.pages)
    else:
        expected = sorted(p for gi, g in enumerate(doc.groups) if gi in only_groups for p in g.pages)
    exported: list[int] = []
    for r in results:
        if r.ok:
            exported.extend(r.pages or r.group.pages)
    issues: list[str] = []
    counts: dict[int, int] = {}
    for p in exported:
        counts[p] = counts.get(p, 0) + 1
    dupes = sorted(p for p, c in counts.items() if c > 1)
    missing = sorted(set(expected) - set(counts))
    extra = sorted(set(counts) - set(expected))
    if missing:
        issues.append(
            f"{doc.name}：以下原页未导出（漏页）："
            + "、".join(str(p) for p in missing[:20])
            + ("……" if len(missing) > 20 else "")
        )
    if dupes:
        issues.append(
            f"{doc.name}：以下原页被导出多次（重复）："
            + "、".join(str(p) for p in dupes[:20])
        )
    if extra:
        issues.append(
            f"{doc.name}：以下页不在任何分组内却被导出："
            + "、".join(str(p) for p in extra[:20])
        )
    return issues


def _export_one(
    doc: ScanDocument,
    group: Group,
    out_dir: Path,
    opts: ExportOptions,
    report: ExportReport,
    index: int,
) -> ExportResult:
    result = ExportResult(group=group, doc_name=doc.name)
    filename = build_filename(
        group.title,
        group.unit,
        template=opts.template or DEFAULT_TEMPLATE,
        index=index,
        orig=doc.name,
        start=group.start,
        end=group.end,
    )
    result.filename = filename

    tmp_path = out_dir / f".~tmp_{uuid.uuid4().hex}.pdf"
    try:
        final_path = unique_path(out_dir, filename)
        result.path = final_path
        reader = PdfReader(str(doc.path))
        writer = PdfWriter()
        for src_page in group.pages:
            if opts.cancel_check and opts.cancel_check():
                report.canceled = True
                raise InterruptedError("用户取消")
            page = reader.pages[src_page - 1]
            # 导出方向 = 源文件原有 /Rotate + 统一方向状态的额外旋转
            # （effective_rotation = 人工覆盖 ?? 自动检测 ?? 0）。
            # 二者必须叠加，才能与渲染器一致（render.py: total = stored + user）——
            # 否则源文件已带 /Rotate 的页面在导出时会丢失该旋转（漏旋转）。
            # 以**绝对目标角度**写入 /Rotate（apply_target_rotation 内部绝对值覆盖），
            # 因此不存在二次旋转；且不重编码图片（pypdf 复制页面对象）。
            ps = doc.pages[src_page - 1]
            target = (int(ps.original_rotation) + int(ps.effective_rotation)) % 360
            apply_target_rotation(page, target)
            writer.add_page(page)
        with open(tmp_path, "wb") as f:
            writer.write(f)
        result.pages = group.pages
        # 页码核对
        check = PdfReader(str(tmp_path))
        if len(check.pages) != group.page_count:
            raise RuntimeError(
                f"页码核对失败：输出 {len(check.pages)} 页，预期 {group.page_count} 页"
            )
        # 保真校验（内容流与嵌入图像）
        page_map = group.pages
        rotations = [(doc.pages[p-1].original_rotation + doc.pages[p-1].effective_rotation) % 360
                     for p in page_map]
        issues = verify.verify_mapping(doc.path, tmp_path, page_map, rotations=rotations)
        if issues:
            raise RuntimeError("保真校验未通过：" + "；".join(issues[:5]))
        if verify.file_sha256(doc.path) != doc.source_sha256:
            raise RuntimeError("源文件在导出过程中发生变化")
        # On Windows rename refuses an existing destination, including a file
        # created after unique_path. Never overwrite another process's output.
        os.rename(tmp_path, final_path)
        result.ok = True
        _log(report, opts, f"[成功] {filename}.pdf（{group.page_count} 页，源第 {group.start}-{group.end} 页，保真校验通过）")
    except InterruptedError:
        _log(report, opts, f"[取消] {filename}.pdf 已停止")
        result.error = "用户取消"
    except Exception as e:
        _log(report, opts, f"[失败] {filename}.pdf：{e}")
        result.error = str(e)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
    return result


def export_documents(
    docs: list[ScanDocument], opts: ExportOptions
) -> ExportReport:
    """批量导出所有文档的全部分组。源文件仅读取，绝不写入。"""
    report = ExportReport()
    out_dir = Path(opts.out_dir)
    hard_issues = integrity_preflight(docs, opts)
    if hard_issues:
        report.coverage_issues["预检"] = hard_issues
        for msg in hard_issues:
            _log(report, opts, f"[阻止导出] {msg}")
        return report
    invalid = [(doc, g) for di, doc in enumerate(docs) for gi, g in enumerate(doc.groups)
               if (opts.only is None or (di, gi) in opts.only) and (g.need_confirm
               or ("{title}" in opts.template and not g.title.strip())
               or ("{unit}" in opts.template and not g.unit.strip()))]
    if invalid:
        for doc, g in invalid:
            report.results.append(ExportResult(g, doc.name, error="标题或填报单位待确认"))
            _log(report, opts, f"[阻止导出] {doc.name} 第 {g.start}-{g.end} 页：名称待确认")
        return report
    out_dir.mkdir(parents=True, exist_ok=True)

    for di, doc in enumerate(docs):
        report.src_sha_before[str(doc.path)] = verify.file_sha256(doc.path)
        # 按清单导出：仅导出被勾选的分组
        only = getattr(opts, "only", None)
        sel_gis = None
        if only is not None:
            sel_gis = {gi for (d_i, gi) in only if d_i == di}
            if not sel_gis:
                continue
            _log(report, opts, f"开始导出 {doc.name}（已勾选 {len(sel_gis)} 份）")
        else:
            _log(report, opts, f"开始导出 {doc.name}（{len(doc.groups)} 份）")
        doc_result_start = len(report.results)
        for gi, group in enumerate(doc.groups):
            if report.canceled:
                break
            if sel_gis is not None and gi not in sel_gis:
                continue  # 用户主动跳过该组
            result = _export_one(doc, group, out_dir, opts, report, gi + 1)
            report.results.append(result)
            if opts.on_progress:
                opts.on_progress(
                    len(report.results), len(doc.groups), f"导出 {doc.name}"
                )
        # 源文件哈希复检
        report.src_sha_after[str(doc.path)] = verify.file_sha256(doc.path)
        if report.src_sha_before[str(doc.path)] != report.src_sha_after[str(doc.path)]:
            _log(report, opts, f"[严重] 源文件 {doc.name} 在处理前后哈希不一致！")
        else:
            _log(report, opts, f"源文件 {doc.name} 完整性校验通过（SHA-256 一致）")
        # 全局页面覆盖率校验（v1.8）：每个有效原页恰好导出一次。
        # 用户主动取消导致的漏页属预期行为，不算校验失败。
        if report.canceled:
            _log(report, opts, f"{doc.name} 导出被取消，跳过页面覆盖率校验。")
        else:
            doc_issues = coverage_issues(
                doc, report.results[doc_result_start:], only_groups=sel_gis
            )
            biz_issues = business_integrity_issues(doc)
            if doc_issues:
                report.coverage_issues[doc.name] = doc_issues
                for msg in doc_issues:
                    _log(report, opts, f"[严重] 页面覆盖率校验：{msg}")
            elif not biz_issues:
                n_pages = sum(
                    g.page_count for gi, g in enumerate(doc.groups)
                    if sel_gis is None or gi in sel_gis
                )
                _log(
                    report, opts,
                    f"页面覆盖率校验通过：{doc.name} 全部 {n_pages} 个有效页恰好导出一次",
                )
            # 业务完整性只在未按清单筛选、或筛选覆盖全部组时适用；
            # 用户主动跳过某些组时，未导出页属预期，不报"静默丢页"。
            if sel_gis is None or len(sel_gis) == len(doc.groups):
                for msg in biz_issues:
                    report.coverage_issues.setdefault(doc.name, []).append(msg)
                    _log(report, opts, f"[严重] 业务完整性校验：{msg}")

    n_ok = len(report.succeeded)
    n_fail = len(report.failed)
    _log(report, opts, f"导出完成：成功 {n_ok} 份，失败 {n_fail} 份。")
    _write_manifest(docs, opts, report, out_dir)
    return report


def _write_manifest(docs, opts, report, out_dir):
    """Persist the disposition of EVERY source page, including omissions and cancellation."""
    sources = []
    outputs = []
    for di, doc in enumerate(docs):
        pages = []
        for ps in doc.pages:
            group_idx = next((gi for gi, g in enumerate(doc.groups) if ps.src_index in g.pages), None)
            result = next((r for r in report.results if group_idx is not None
                           and r.group is doc.groups[group_idx]), None)
            if result and result.ok:
                status = "exported"
            elif group_idx is None:
                status = ("excluded_manual" if ps.src_index in doc.manual_separator_pages else
                          "excluded_blank_rule" if blankpolicy.valid_exclusion(ps) else "excluded_context")
            elif opts.only is not None and (di, group_idx) not in opts.only:
                status = "unselected"
            else:
                status = "canceled" if report.canceled else "failed"
            pages.append(dict(source_page=ps.src_index, status=status,
                output=str(result.path) if result and result.ok else None,
                output_page=result.pages.index(ps.src_index)+1 if result and result.ok else None,
                rotation=(ps.original_rotation+ps.effective_rotation)%360,
                note="明确空白：整页含页边图像复核，无文本层；从输出删除" if blankpolicy.valid_exclusion(ps) else "",
                blank_exclusion_evidence=ps.blank_exclusion_evidence if ps.omitted_blank else None,
                manual_separator=ps.src_index in doc.manual_separator_pages))
        sources.append(dict(path=str(doc.path), imported_sha256=doc.source_sha256,
            after_sha256=report.src_sha_after.get(str(doc.path)), page_count=doc.page_count, pages=pages))
    try:
        for r in report.succeeded:
            outputs.append(dict(path=str(r.path), sha256=verify.file_sha256(r.path), pages=r.pages,
                                title=r.group.title, reporting_unit=r.group.unit))
        path = out_dir / f"ScanSplit-导出记录-{uuid.uuid4().hex[:12]}.json"
        payload = dict(schema=1, created_at=datetime.now(timezone.utc).isoformat(),
                       canceled=report.canceled, sources=sources, outputs=outputs,
                       issues=report.coverage_issues, logs=report.logs)
        with path.open("x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        report.manifest_path = path
        _log(report, opts, f"页面去向记录：{path.name}")
    except OSError as exc:
        report.manifest_error = str(exc)
        _log(report, opts, f"[注意] 页面去向记录写入失败：{exc}")
