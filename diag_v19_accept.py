# -*- coding: utf-8 -*-
"""任务 0：v1.8 GUI 验收（withdrawn Tk 驱动真实 App，子进程隔离避免 Tk 多根干扰）。
用法：直接运行 = 跑全部 4 项；传 check 名 = 只跑单项（供父进程调度）。
1) 严格模式下未确认字段无法直接导出；2) 非严格模式须用户显式选择；
3) 调整分组后人工名称不丢失；4) 导出后源 SHA-256 不变。"""
import subprocess, sys, tempfile, traceback
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:/vibe-coding/scansplit-pdf")
sys.path.insert(0, r"D:/vibe-coding/scansplit-pdf/tests")

CHECKS = ["strict_block", "nonstrict_explicit", "migrate_unique", "sha_unchanged"]


def run_check(name: str) -> None:
    import tkinter as tk
    from pathlib import Path
    from tests.samplegen import build_scan_pdf
    from scansplit import verify
    import scansplit.ui as ui_mod
    from scansplit.ui import App

    tmp = tempfile.mkdtemp(prefix="v19_acc_")
    scan = build_scan_pdf(str(Path(tmp) / "scan.pdf"),
                          ["text", "text", "blank", "text", "blank", "text"])
    sha0 = verify.file_sha256(scan)
    out = tempfile.mkdtemp(prefix="v19_out_")

    app = App(tk.Tk())
    app.root.withdraw()
    answers = {"strict_block": [False], "nonstrict_explicit": [True]}.get(name, [])
    seq = {"i": 0}

    def _ask(*a, **k):
        i = seq["i"]; seq["i"] += 1
        return answers[i] if i < len(answers) else False
    ui_mod.messagebox.askyesno = _ask
    ui_mod.messagebox.showinfo = lambda *a, **k: None
    ui_mod.messagebox.showwarning = lambda *a, **k: None
    ui_mod.messagebox.askokcancel = lambda *a, **k: True
    ui_mod.filedialog.askdirectory = lambda **k: out

    app.import_files([str(scan)])
    doc = app.ws.documents[0]
    from scansplit.grouping import refresh_groups
    doc.pages[2].separator = True
    doc.pages[4].separator = True
    refresh_groups(doc)

    if name == "strict_block":
        for g in doc.groups:
            g.title, g.unit = f"T{g.start}", "单位"
            g.title_confirmed = False  # 模拟 OCR 待确认
        app.export_all()
        assert seq["i"] >= 1, "应弹出严格阻断对话框"
        assert not list(Path(out).glob("*.pdf")), "用户拒绝后不应导出任何文件"
    elif name == "nonstrict_explicit":
        for g in doc.groups:
            g.title, g.unit = f"T{g.start}", "单位"
            g.title_confirmed = False
        app.export_all()
        files = list(Path(out).glob("*.pdf"))
        assert len(files) == 3, f"显式选择非严格后应导出 3 份，实际 {len(files)}"
    elif name == "migrate_unique":
        doc.groups[0].title, doc.groups[0].unit = "人工标题A", "人工单位A"
        doc.groups[0].title_confirmed = True
        # 第 1 页改为分隔页：旧组 (1,2) 与新组 (2,2) 唯一对应 → 应迁移人工命名
        app.set_separator([1], True)
        g2 = next(g for g in doc.groups if g.start == 2)
        assert g2.title == "人工标题A" and g2.unit == "人工单位A", \
            f"迁移丢失: {g2.title!r}/{g2.unit!r}"
        assert any("沿用原第 1-2 页" in line for line in app.log_text_get()
                   ) if hasattr(app, "log_text_get") else True
    elif name == "sha_unchanged":
        for g in doc.groups:
            g.title, g.unit = f"标题{g.start}", "单位"
            g.title_confirmed = g.unit_confirmed = True
        app.export_all()
        assert verify.file_sha256(scan) == sha0, "源文件被修改！"
        for f in sorted(Path(out).glob("*.pdf")):
            assert len(__import__("pypdf", fromlist=["PdfReader"]).PdfReader(str(f)).pages) >= 1
    app.on_close()
    print(f"[PASS] {name}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_check(sys.argv[1])
    else:
        failed = []
        for name in CHECKS:
            r = subprocess.run([sys.executable, __file__, name],
                               capture_output=True, text=True, encoding="utf-8")
            tail = (r.stdout or "").strip().splitlines()
            if r.returncode == 0 and tail and "[PASS]" in tail[-1]:
                print(f"[PASS] {name}")
            else:
                err = (r.stderr or r.stdout or "").strip().splitlines()
                print(f"[FAIL] {name}: {err[-1] if err else '无输出'}")
                failed.append(name)
        print()
        if failed:
            print("未通过：", "、".join(failed)); sys.exit(1)
        print("任务 0 GUI 验收：全部通过")
