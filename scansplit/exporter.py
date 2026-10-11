"""批量导出：pypdf 复制页面对象（保真）、写入 /Rotate、命名、冲突保护、
进度/日志/取消、每份页码核对、输出后自动保真校验。源文件只读不改。"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from . import verify
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
    # 严格导出模式（v1.8 默认开启）：空标题/空单位/存在待确认字段时
    # preflight 返回阻断项。非严格模式仅用于测试文件，须由用户显式选择。
    strict: bool = True


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
            if opts.strict and g.need_confirm:
                issues.append(
                    f"{doc.name} 文档{gi + 1}（第{g.start}-{g.end}页）："
                    "存在待确认字段（标题/单位），严格模式下阻止导出"
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
      * 否则（uncertain / 无上下文判定 / 视觉空白但未确认）→ **禁止静默排除**，
        列为问题，阻断导出。
    返回问题字符串列表（空 = 通过）。
    """
    in_group = {p for g in doc.groups for p in g.pages}
    manual = getattr(doc, "manual_separator_pages", None) or set()
    issues: list[str] = []
    for st in doc.pages:
        if st.src_index in in_group:
            continue  # 已导出
        # 未在分组内 → 被排除。必须有明确、经确认的排除依据。
        confirmed = (
            st.src_index in manual
            or getattr(st, "blank_context", "") == "separator"
        )
        if not confirmed:
            ctx = getattr(st, "blank_context", "") or "未判定"
            issues.append(
                f"{doc.name} 第 {st.src_index} 页未被导出，且没有经确认的排除依据"
                f"（业务属性：{ctx}）——禁止静默丢页"
            )
    return issues


def coverage_issues(doc: ScanDocument, results: list[ExportResult]) -> list[str]:
    """全局页面覆盖率校验（v1.8）：每个有效原页（组内页）必须恰好导出一次。

    漏导、重复导出、导出了组外页均视为问题。空白页只要未标记为分隔页，
    就属于某个分组、必须出现在输出中（防止"内部空白页被误删"）。
    """
    expected = sorted(p for g in doc.groups for p in g.pages)
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
        for src_page in range(group.start, group.end + 1):
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
        result.pages = list(range(group.start, group.end + 1))
        # 页码核对
        check = PdfReader(str(tmp_path))
        if len(check.pages) != group.page_count:
            raise RuntimeError(
                f"页码核对失败：输出 {len(check.pages)} 页，预期 {group.page_count} 页"
            )
        # 保真校验（内容流与嵌入图像）
        page_map = list(range(group.start, group.end + 1))
        issues = verify.verify_mapping(doc.path, tmp_path, page_map)
        if issues:
            raise RuntimeError("保真校验未通过：" + "；".join(issues[:5]))
        os.replace(tmp_path, final_path)
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
    out_dir.mkdir(parents=True, exist_ok=True)

    for doc in docs:
        report.src_sha_before[str(doc.path)] = verify.file_sha256(doc.path)
        _log(report, opts, f"开始导出 {doc.name}（{len(doc.groups)} 份）")
        doc_result_start = len(report.results)
        for gi, group in enumerate(doc.groups):
            if report.canceled:
                break
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
            doc_issues = coverage_issues(doc, report.results[doc_result_start:])
            biz_issues = business_integrity_issues(doc)
            if doc_issues:
                report.coverage_issues[doc.name] = doc_issues
                for msg in doc_issues:
                    _log(report, opts, f"[严重] 页面覆盖率校验：{msg}")
            elif not biz_issues:
                _log(
                    report, opts,
                    f"页面覆盖率校验通过：{doc.name} 全部 {sum(g.page_count for g in doc.groups)} 个有效页恰好导出一次",
                )
            for msg in biz_issues:
                report.coverage_issues.setdefault(doc.name, []).append(msg)
                _log(report, opts, f"[严重] 业务完整性校验：{msg}")

    n_ok = len(report.succeeded)
    n_fail = len(report.failed)
    _log(report, opts, f"导出完成：成功 {n_ok} 份，失败 {n_fail} 份。")
    return report
