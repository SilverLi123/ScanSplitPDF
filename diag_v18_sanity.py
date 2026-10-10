# -*- coding: utf-8 -*-
"""v1.8 真实样本端到端 sanity：分组 → 严格预检 → 导出 → 覆盖率/保真/源哈希。"""
import sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:/vibe-coding/scansplit-pdf")

from scansplit.loader import open_document
from scansplit.model import ScanDocument
from scansplit.grouping import refresh_groups
from scansplit.exporter import ExportOptions, export_documents, preflight, coverage_issues
from scansplit import verify

SRC = r"C:/Users/li333/Downloads/%E7%BA%AF%E5%9B%BE%E7%89%87PDF_%E5%A4%9A%E6%96%87%E6%A1%A3%E7%A9%BA%E7%99%BD%E9%A1%B5%E5%88%86%E9%9A%94_%E6%B5%8B%E8%AF%95%E6%A0%B7%E6%9C%AC.pdf"
sha0 = verify.file_sha256(SRC)

reader, warns = open_document(SRC)
doc = ScanDocument(SRC, reader, warns)
for i in (2, 6, 7, 10):  # 0-based：第 3/7/8/11 页为空白分隔页
    doc.pages[i].separator = True
groups = refresh_groups(doc)
print("分组:", [(g.start, g.end) for g in groups])
assert [(g.start, g.end) for g in groups] == [(1, 2), (4, 6), (9, 10)]

titles = [("XX项目通知", "教务处"), ("季度报表（侧放）", "财务处"), ("会议纪要", "校长办公室")]
for g, (t, u) in zip(groups, titles):
    g.title, g.unit = t, u
    g.title_confirmed = g.unit_confirmed = True

# 严格模式预检（含未确认字段阻断路径）
opts = ExportOptions(out_dir=tempfile.mkdtemp(prefix="scansplit_v18_"), strict=True)
issues = preflight([doc], opts)
print("严格预检:", issues or "通过")
assert issues == []

# 未确认字段必须被阻断
groups[1].title_confirmed = False
issues2 = preflight([doc], opts)
assert any("待确认" in i for i in issues2), issues2
print("未确认字段阻断: OK ->", issues2[0])
groups[1].title_confirmed = True

t0 = time.perf_counter()
report = export_documents([doc], opts)
print(f"导出 {time.perf_counter()-t0:.1f}s: 成功 {len(report.succeeded)} 失败 {len(report.failed)}")
assert not report.failed, [r.error for r in report.failed]
assert not report.coverage_issues, report.coverage_issues
print("覆盖率校验: 通过（11 页中 7 个有效页恰好各导出一次）")
assert verify.file_sha256(SRC) == sha0
print("源文件 SHA-256 不变: OK")
for r in report.succeeded:
    print("  ", r.path.name, f"{len(r.pages)} 页")
print("\n全部 sanity 通过")
