"""主界面：导入 → 空白检测 → 分组 → OCR 命名 → 方向/边界复核 → 无损导出。

工作流原则（与核心模块一致）：
- 空白检测是第一拆分依据；OCR 首页标题检测是第二验证信号，不一致时提示确认；
- 旋转只设置目标 /Rotate，导出绝不重绘页面；
- OCR 全程本地离线运行，识别结果只作建议，可人工修改。
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import autoname, blankdetect, exporter, grouping, loader, naming, rotation, settings, splitctx
from .loader import LoadError
from .model import ScanDocument, Workspace
from .ocr import OcrEngine
from .render import PageRenderer, RenderError
from .tasks import TaskManager
from .ui_widgets import (
    GroupPanelArea,
    LogPanel,
    PreviewPane,
    ProgressDialog,
    ThumbnailStrip,
    enable_drag_drop,
)

UNDO_CAP = 100


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.ws = Workspace()
        self.renderers: dict[str, PageRenderer] = {}
        self.settings = settings.load_settings()
        self.detect_params = settings.load_detect_params(self.settings)
        self.unit_history = settings.load_unit_history(self.settings)
        self.template_var = tk.StringVar(value=self.settings.get("template") or naming.DEFAULT_TEMPLATE)
        self.doc_index = 0
        self.current_page = 1
        self.sel_pages: set[int] = set()
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._busy = False
        # v1.9 后台任务：单工作线程 + Queue/root.after 回传（UI 不冻结）
        self.tasks = TaskManager(root, self.log)
        self.tasks.progress_hook = self._on_task_progress
        self._prog = None

        self._build_ui()
        try:
            self.preview.zoom = min(
                max(float(self.settings.get("zoom", 1.0)), PreviewPane.MIN_ZOOM),
                PreviewPane.MAX_ZOOM,
            )
        except (TypeError, ValueError):
            self.preview.zoom = 1.0
        self.preview.fit_mode = bool(self.settings.get("fit_mode", True))
        self.update_status()
        enable_drag_drop(self.root, self.import_files)
        self.log("就绪。请打开 PDF 文件（可多选或直接拖入窗口）。")

    # -- 界面构建 -----------------------------------------------------------

    def _build_ui(self):
        root = self.root
        root.title("ScanSplit PDF — 扫描件拆分与命名工具")

        tools = ttk.Frame(root, padding=(6, 4))
        tools.pack(fill="x")
        # 工具栏按工作流分三段（参考 pdf-split-and-rename 的步骤化流程）：
        # ①拆分 → ②命名/检查 → ③导出；未就绪的按钮自动置灰（见 _update_step_state）。
        self.tool_btns: dict[str, ttk.Button] = {}

        def tool_section(title: str, cmds: list[tuple[str, str, object]]):
            ttk.Label(tools, text=title, foreground="#5d6d7e",
                      font=("Segoe UI", 9, "bold")).pack(side="left", padx=(10, 2))
            for key, text, cmd in cmds:
                b = ttk.Button(tools, text=text, command=cmd)
                b.pack(side="left", padx=2)
                self.tool_btns[key] = b

        tool_section("① 拆分", [
            ("open", "打开 PDF…", self.open_dialog),
            ("detect", "检测空白页", self.detect_blank),
            ("group", "自动分组", self.auto_group_all),
        ])
        tool_section("② 命名 / 检查", [
            ("ocr", "OCR 自动命名（本地）", self.ocr_name),
            ("direction", "识别方向", self.direction_analyze),
            ("review", "复核拆分/补拆", self.boundary_review_action),
        ])
        tool_section("③ 导出", [
            ("export", "导出全部…", self.export_all),
        ])
        tool_section("其他", [
            ("undo", "撤销", self.undo),
            ("redo", "重做", self.redo),
            ("settings", "设置…", self.open_settings),
        ])

        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=4, pady=2)

        left = ttk.Frame(paned)
        files_frame = ttk.LabelFrame(left, text="已导入文件", padding=4)
        files_frame.pack(fill="x", padx=2)
        self.file_list = tk.Listbox(files_frame, height=4, exportselection=False)
        self.file_list.pack(fill="x")
        self.file_list.bind("<<ListboxSelect>>", self._on_file_select)
        self.file_list.bind("<Button-3>", self._on_file_menu)
        ttk.Label(left, text="页面缩略图（右键：分隔/旋转；Ctrl/Shift 多选）").pack(anchor="w", padx=4, pady=(4, 0))
        self.thumbs = ThumbnailStrip(left, self)
        self.thumbs.pack(fill="both", expand=True, padx=2, pady=(0, 4))

        self.preview = PreviewPane(paned, self)

        right = ttk.LabelFrame(paned, text="文档分组（标题/单位可修改）", padding=2)
        self.groups = GroupPanelArea(right, self)
        self.groups.pack(fill="both", expand=True)

        paned.add(left, weight=1)
        paned.add(self.preview, weight=4)
        paned.add(right, weight=3)

        bottom = ttk.Frame(root, padding=(6, 0, 6, 4))
        bottom.pack(fill="x")
        status_row = ttk.Frame(bottom)
        status_row.pack(fill="x")
        self.status_var = tk.StringVar()
        ttk.Label(status_row, textvariable=self.status_var, anchor="w").pack(
            side="left", fill="x", expand=True)
        self.hint_var = tk.StringVar()
        ttk.Label(status_row, textvariable=self.hint_var, foreground="#1a5276",
                  font=("Segoe UI", 9, "bold")).pack(side="right")
        self.log_panel = LogPanel(bottom)
        self.log_panel.pack(fill="x", pady=(2, 0))

        root.bind("<Control-o>", lambda e: self.open_dialog())
        root.bind("<Control-z>", lambda e: self.undo(from_key=True))
        root.bind("<Control-y>", lambda e: self.redo(from_key=True))
        root.bind("<Control-e>", lambda e: self.export_all())
        root.bind("<Left>", lambda e: self._nav_key(-1))
        root.bind("<Right>", lambda e: self._nav_key(1))
        root.bind("<Delete>", lambda e: self._delete_key())

    # -- 基础状态 -----------------------------------------------------------

    def log(self, msg: str):
        self.log_panel.log(msg)

    def _active_doc(self) -> ScanDocument | None:
        if not self.ws.documents:
            return None
        return self.ws.documents[min(self.doc_index, len(self.ws.documents) - 1)]

    def _editing(self) -> bool:
        w = self.root.focus_get()
        return w is not None and w.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox", "TSpinbox", "TScale")

    def update_status(self):
        docs = self.ws.documents
        total_pages = sum(d.page_count for d in docs)
        blanks = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "blank")
        maybes = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "maybe")
        n_groups = sum(len(d.groups) for d in docs)
        n_conf = sum(1 for d in docs for g in d.groups if g.need_confirm)
        cur = ""
        doc = self._active_doc()
        if doc:
            cur = f" · 当前 {doc.name} 第 {self.current_page}/{doc.page_count} 页"
        self.status_var.set(
            f"文件 {len(docs)} · 共 {total_pages} 页 · 空白候选 {blanks} · 疑似 {maybes}"
            f" · 分组 {n_groups} · 待确认 {n_conf}{cur}"
        )
        self._update_step_state(len(docs), blanks + maybes > 0, n_groups, n_conf)

    def _update_step_state(
        self,
        n_docs: int | None = None,
        has_blank_analysis: bool | None = None,
        n_groups: int | None = None,
        n_conf: int | None = None,
    ):
        """按工作流阶段启用/置灰工具栏按钮，并在状态栏给出“下一步”提示。
        参数缺省时从工作区现算（后台任务收尾等场景直接调用）。"""
        if n_docs is None:
            docs = self.ws.documents
            blanks = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "blank")
            maybes = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "maybe")
            n_docs = len(docs)
            has_blank_analysis = blanks + maybes > 0
            n_groups = sum(len(d.groups) for d in docs)
            n_conf = sum(1 for d in docs for g in d.groups if g.need_confirm)
        readiness = {
            "detect": n_docs > 0,
            "group": n_docs > 0 and has_blank_analysis,
            "ocr": n_docs > 0 and n_groups > 0,
            "direction": n_docs > 0 and n_groups > 0,
            "review": n_docs > 0 and n_groups > 0,
            "export": n_docs > 0 and n_groups > 0,
        }
        for key, ready in readiness.items():
            btn = self.tool_btns.get(key)
            if btn is not None:
                btn.state(["!disabled"] if ready else ["disabled"])
        if not n_docs:
            hint = "下一步：打开 PDF（可拖入窗口）"
        elif not has_blank_analysis:
            hint = "下一步：检测空白页"
        elif not n_groups:
            hint = "下一步：自动分组"
        elif n_conf:
            hint = "下一步：确认橙色“待确认”字段，然后导出"
        else:
            hint = "下一步：识别方向 / 复核拆分（可选），然后导出全部"
        self.hint_var.set(hint)

    def _refresh_all(self):
        self._rebuild_file_list()
        doc = self._active_doc()
        if doc:
            self.current_page = min(max(self.current_page, 1), doc.page_count)
            if not self.sel_pages:
                self.sel_pages = {self.current_page}
            renderer = self.renderers[str(doc.path)]
            self.thumbs.set_document(doc, renderer)
            self.preview.show(doc, renderer, self.current_page)
            self.groups.set_document(doc)
        else:
            self.thumbs.set_document(None, None)
            self.preview.show_guide()
            self.groups.set_document(None)
        self.update_status()

    def _rebuild_file_list(self):
        self.file_list.delete(0, "end")
        for d in self.ws.documents:
            self.file_list.insert("end", f"{d.name}（{d.page_count} 页 · {len(d.groups)} 组）")
        if self.ws.documents:
            self.file_list.selection_clear(0, "end")
            self.file_list.selection_set(self.doc_index)
            self.file_list.see(self.doc_index)

    # -- 撤销/重做 ------------------------------------------------------------

    def _push_undo(self):
        self.undo_stack.append(self.ws.snapshot())
        if len(self.undo_stack) > UNDO_CAP:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    # -- 后台任务辅助（v1.9） ---------------------------------------------------

    def _on_task_progress(self, title, done, total, phase):
        """主线程进度回调：懒创建进度框并刷新（来自 TaskManager 轮询）。"""
        try:
            if self._prog is None or not self._prog.winfo_exists():
                self._prog = ProgressDialog(self.root, title or "处理中")
            self._prog.set(done, total, phase)
        except tk.TclError:
            pass  # 窗口正在关闭

    def _end_progress(self):
        if self._prog is not None:
            try:
                self._prog.destroy()
            except tk.TclError:
                pass
            self._prog = None

    def _task_finish(self):
        """on_done 回调统一收尾：关进度框、解除忙碌、刷新按钮状态。"""
        self._end_progress()
        self._busy = False
        self._update_step_state()
        self.update_status()

    def _doc_still_open(self, doc) -> bool:
        """文档对象仍在工作区（防移除文档后旧任务结果写回）。"""
        return any(d is doc for d in self.ws.documents)

    def _restore_ws_snapshot(self, snap: dict):
        """取消/异常后恢复工作区（已移除的文档自然跳过，绝不跨文档写入）。"""
        self.ws.restore(snap)

    def _start_task(self, kind, work, on_done, title) -> bool:
        if not self.tasks.start(kind, work, on_done, title=title):
            messagebox.showinfo("请稍候", "当前已有任务在后台运行，请等待完成或先取消。")
            return False
        self._busy = True
        self._update_step_state()
        return True

    def undo(self, from_key: bool = False):
        if self._busy or self.tasks.busy or (from_key and self._editing()):
            return
        if not self.undo_stack:
            self.log("没有可撤销的操作。")
            return
        self.redo_stack.append(self.ws.snapshot())
        self.ws.restore(self.undo_stack.pop())
        self._refresh_all()
        self.log("已撤销上一步操作。")

    def redo(self, from_key: bool = False):
        if self._busy or self.tasks.busy or (from_key and self._editing()):
            return
        if not self.redo_stack:
            self.log("没有可重做的操作。")
            return
        self.undo_stack.append(self.ws.snapshot())
        self.ws.restore(self.redo_stack.pop())
        self._refresh_all()
        self.log("已重做。")

    # -- 导入 -----------------------------------------------------------------

    def open_dialog(self):
        paths = filedialog.askopenfilenames(
            title="选择要处理的 PDF 文件（可多选）",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if paths:
            self.import_files(list(paths))

    def import_files(self, paths: list[str]):
        if self._busy:
            return
        added, errors = 0, []
        for path_str in paths:
            path = Path(path_str)
            if not path.name.lower().endswith(".pdf"):
                continue
            if any(d.path == path for d in self.ws.documents):
                self.log(f"已导入，跳过：{path.name}")
                continue
            try:
                reader, warnings = loader.open_document(path)
                renderer = PageRenderer(path)
            except (LoadError, RenderError) as e:
                errors.append(str(e))
                continue
            doc = ScanDocument(path, reader, warnings)
            grouping.refresh_groups(doc)
            self.ws.documents.append(doc)
            self.renderers[str(path)] = renderer
            self.log(f"导入 {path.name}（{doc.page_count} 页）")
            for w in warnings:
                self.log(f"警告：{w}")
            added += 1
        if errors:
            messagebox.showwarning(
                "部分文件无法导入",
                "\n\n".join(errors[:8]) + ("\n……" if len(errors) > 8 else ""),
            )
        if added:
            self.doc_index = len(self.ws.documents) - 1
            self.current_page = 1
            self.sel_pages = {1}
            self._refresh_all()

    def _on_file_select(self, _e=None):
        sel = self.file_list.curselection()
        if not sel:
            return
        self.doc_index = sel[0]
        self.current_page = 1
        self.sel_pages = {1}
        self._refresh_all()

    def _on_file_menu(self, e):
        idx = self.file_list.nearest(e.y)
        if idx < 0 or idx >= len(self.ws.documents):
            return
        self.file_list.selection_clear(0, "end")
        self.file_list.selection_set(idx)
        self.doc_index = idx
        doc = self.ws.documents[idx]
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"移除 {doc.name}", command=lambda: self.remove_doc(idx))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def remove_doc(self, idx: int):
        if self._busy or idx >= len(self.ws.documents):
            return
        doc = self.ws.documents[idx]
        if not messagebox.askyesno(
            "移除文件",
            f"从工作区移除 {doc.name}？\n（不会修改或删除源文件，可重新导入）",
        ):
            return
        self.ws.documents.remove(doc)
        renderer = self.renderers.pop(str(doc.path), None)
        if renderer:
            renderer.close()
        self.doc_index = min(self.doc_index, len(self.ws.documents) - 1)
        self.current_page = 1
        self.sel_pages = set()
        self._refresh_all()
        self.log(f"已从工作区移除 {doc.name}（源文件未改动）。")

    # -- 空白检测与分组 --------------------------------------------------------

    def detect_blank(self):
        if self._busy:
            return
        docs = list(self.ws.documents)
        if not docs:
            messagebox.showinfo("检测空白页", "请先打开 PDF 文件。")
            return
        self._push_undo()
        snap = self.ws.snapshot()
        params = self.detect_params
        total = sum(d.page_count for d in docs)

        def work(progress, log, cancel):
            # 渲染器在 worker 线程内自建自毁（线程归属约束，不碰主线程渲染器）
            out = []
            done_base = 0
            for d in docs:
                renderer = PageRenderer(d.path)
                try:
                    def has_text(i, reader=d.reader) -> bool:
                        try:
                            return len((reader.pages[i].extract_text() or "").strip()) > 10
                        except Exception:
                            return False

                    def cb(i, n, dd=d):
                        progress(done_base + i, total, f"检测 {dd.name} 第 {i}/{n} 页")

                    res = blankdetect.detect_document(
                        renderer, d.page_count, params, has_text, cb, cancel,
                    )
                finally:
                    renderer.close()
                out.append((d, res))
                done_base += d.page_count
                if cancel():
                    break
            return out

        def on_done(results, canceled, error):
            self._task_finish()
            if error:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"空白检测失败：{tail}（已回滚，无半更新状态）")
                messagebox.showerror("检测空白页", f"检测失败：{tail}")
                return
            if canceled:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                self.log("空白检测已取消，未保存任何结果。")
                return
            for doc, res in results:
                if not self._doc_still_open(doc):
                    self.log(f"{doc.name} 已从工作区移除，检测结果丢弃。")
                    continue
                for page, r in zip(doc.pages, res):
                    page.blank = r
                doc.status = "analyzed"
            self._refresh_all()
            n_blank = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "blank")
            n_maybe = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "maybe")
            # 分文件明细日志：避免多文档时汇总数与当前文件页数对不上造成困惑
            for d in docs:
                db = sum(1 for p in d.pages if p.blank and p.blank.verdict == "blank")
                dm = sum(1 for p in d.pages if p.blank and p.blank.verdict == "maybe")
                self.log(f"{d.name}：空白候选 {db} 页、疑似 {dm} 页（共 {d.page_count} 页）")
            self.log(f"空白检测完成（全部文件合计）：空白候选 {n_blank} 页、疑似 {n_maybe} 页。")
            messagebox.showinfo(
                "检测完成",
                f"空白候选 {n_blank} 页（蓝色“空白”徽章），疑似空白 {n_maybe} 页（橙色“疑似”徽章）。\n\n"
                "下一步：点击“自动分组”把空白候选页作为拆分点；\n"
                "疑似页面请在缩略图上右键人工确认（标记/取消分隔页）。",
            )

        self._start_task("detect", work, on_done, title="检测空白页")

    def auto_group_all(self):
        if self._busy:
            return
        docs = list(self.ws.documents)
        if not docs:
            messagebox.showinfo("自动分组", "请先打开 PDF 文件。")
            return
        if not any(p.blank is not None for d in docs for p in d.pages):
            messagebox.showinfo("自动分组", "尚未检测空白页。请先运行“检测空白页”。")
            return
        self._push_undo()
        snap = self.ws.snapshot()

        # A0：上下文判定需要 OCR 才能区分「分隔空白」与「内部空白」。
        # 引擎不可用时退回"保守模式"：仅把已明确带上下文结论的页作分隔，
        # 其余空白候选一律保留（uncertain），绝不静默丢页。
        engine = OcrEngine.get()
        use_ocr = engine.available
        total_pages = sum(d.page_count for d in docs)

        def work(progress, log, cancel):
            out = []  # [(doc, counts, ctx_counts)]
            done_base = 0
            for d in docs:
                renderer = PageRenderer(d.path) if use_ocr else None
                try:
                    if use_ocr:
                        def cb(i, n, dd=d):
                            progress(done_base + i, total_pages,
                                     f"分析 {dd.name} 拆分上下文 {i}/{n}")
                        evs = splitctx.judge_blank_context(
                            d, renderer, engine, progress_cb=cb, cancel_cb=cancel,
                        )
                        ctx_counts = splitctx.apply_context(d, evs)
                    else:
                        # OCR 不可用：不做上下文判定，空白候选一律保留（保守）
                        ctx_counts = {splitctx.SEPARATOR: [],
                                      splitctx.INTERNAL_BLANK: [],
                                      splitctx.UNCERTAIN: []}
                    grouping.auto_group(d, context_aware=True)
                    out.append((d, ctx_counts))
                finally:
                    if renderer is not None:
                        renderer.close()
                done_base += d.page_count
                if cancel():
                    break
            return out

        def on_done(results, canceled, error):
            self._task_finish()
            if error:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"自动分组失败：{tail}（已回滚，无半更新状态）")
                messagebox.showerror("自动分组", f"自动分组失败：{tail}")
                return
            if canceled:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                self.log("自动分组已取消，未保存任何结果。")
                return
            n_int = 0
            n_unc = 0
            for doc, ctx_counts in results:
                if not self._doc_still_open(doc):
                    continue
                doc.status = "grouped"
                n_int += len(ctx_counts.get(splitctx.INTERNAL_BLANK, []))
                n_unc += len(ctx_counts.get(splitctx.UNCERTAIN, []))
                self.log(f"{doc.name}：分隔页 {sorted(doc.separators)}，分组 {len(doc.groups)} 份")
            self._refresh_all()
            if not use_ocr:
                self.log("OCR 不可用：已按保守模式分组（空白候选默认保留，需人工确认分隔）。")
            tail = ""
            if n_int or n_unc:
                tail = (f"\n\n已保护 {n_int} 页内部空白、{n_unc} 页待确认空白（默认保留在文档内，"
                        "未作为分隔页删除）。")
            messagebox.showinfo(
                "自动分组完成",
                "已结合前后页上下文识别分隔点：内部空白与待确认页均保留在文档内。" + tail +
                "\n可在缩略图上右键调整分隔页；建议点击“复核拆分”做双重验证。",
            )

        self._start_task("autogroup", work, on_done, title="自动分组（上下文判定）")

    def _log_migrate_notes(self, doc):
        """转述 refresh_groups 的迁移/歧义说明（v1.8：人工编辑不静默丢失）。"""
        for note in getattr(doc, "group_migrate_notes", None) or []:
            self.log(f"分组调整：{note}")

    def set_separator(self, pages: list[int], value: bool):
        doc = self._active_doc()
        if not doc:
            return
        self._push_undo()
        for p in pages:
            if 1 <= p <= doc.page_count:
                doc.pages[p - 1].separator = value
        grouping.refresh_groups(doc)
        self._refresh_all()
        self._log_migrate_notes(doc)
        self.log(f"第 {','.join(map(str, sorted(pages)))} 页：{'标记为分隔页' if value else '取消分隔标记'}")

    def add_split_before(self, page_no: int, doc=None):
        """在第 page_no 页前新增拆分边界（页本身保留在组内，绝不丢页）。"""
        doc = doc or self._active_doc()
        if not doc or page_no <= 1:
            return
        self._push_undo()
        doc.pages[page_no - 1].split_before = True
        grouping.refresh_groups(doc)
        self._refresh_all()
        self._log_migrate_notes(doc)
        self.log(f"已在第 {page_no} 页前新增拆分点")

    def show_blank_detail(self, page_no: int):
        doc = self._active_doc()
        if not doc or doc.pages[page_no - 1].blank is None:
            return
        page = doc.pages[page_no - 1]
        extra = "\n".join(f"{k}: {v}" for k, v in sorted(page.blank.signals.items()))
        messagebox.showinfo(f"第 {page_no} 页空白判定", f"{page.blank.reason_cn()}\n\n信号明细：\n{extra}")

    # -- OCR 命名 / 方向 / 边界复核 -------------------------------------------

    def ocr_name(self):
        if self._busy:
            return
        docs = list(self.ws.documents)
        if not docs:
            messagebox.showinfo("OCR 自动命名", "请先打开 PDF 文件。")
            return
        if not any(d.groups for d in docs):
            messagebox.showinfo("OCR 自动命名", "尚未分组。请先运行“自动分组”。")
            return
        engine = OcrEngine.get()
        text_layer_only = False
        if not engine.available:
            if not messagebox.askyesno(
                "本地 OCR 不可用",
                f"{engine.error or '未安装本地 OCR 组件'}\n\n"
                "是否仍尝试从含文本层的 PDF 提取标题/单位？\n"
                "（扫描件将标记“待确认”，需手动填写；OCR 离线安装说明见 README）",
            ):
                return
            text_layer_only = True
        self._push_undo()
        snap = self.ws.snapshot()
        total_g = sum(len(d.groups) for d in docs)
        use_ocr = not text_layer_only

        def work(progress, log, cancel):
            # 渲染器在 worker 线程内自建自毁；OCR 引擎为懒加载单例，
            # 仅由本工作线程使用（主线程不做 OCR），无并发冲突。
            done_base = 0
            for d in docs:
                if not d.groups:
                    continue
                renderer = PageRenderer(d.path) if use_ocr else None
                try:
                    def cb(i, n, dd=d):
                        progress(done_base + i, total_g, f"命名 {dd.name} 第 {i}/{n} 组")

                    autoname.auto_name_groups(
                        d, renderer, engine=engine if use_ocr else None,
                        progress_cb=cb, cancel_cb=cancel,
                    )
                finally:
                    if renderer is not None:
                        renderer.close()
                done_base += len(d.groups)
                if cancel():
                    break
            return True

        def on_done(_result, canceled, error):
            self._task_finish()
            if error:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"OCR 自动命名失败：{tail}（已回滚，无半更新状态）")
                messagebox.showerror("OCR 自动命名", f"命名失败：{tail}")
                return
            if canceled:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                self.log("OCR 自动命名已取消，未保存任何修改。")
                return
            # 文档被移除的组不进历史
            for doc in docs:
                if self._doc_still_open(doc):
                    for g in doc.groups:
                        if g.unit.strip():
                            self.unit_history.add(g.unit)
            self._refresh_all()
            n_conf = sum(1 for d in docs if self._doc_still_open(d)
                         for g in d.groups if g.need_confirm)
            if text_layer_only:
                self.log(f"文本层提取完成：共 {total_g} 组，待确认 {n_conf} 组（结果见右侧面板）。")
                return
            self.log(f"OCR 自动命名完成：待确认 {n_conf} 组。")
            if n_conf:
                messagebox.showinfo(
                    "OCR 自动命名完成",
                    f"共 {total_g} 组，其中 {n_conf} 组存在不确定字段（右侧面板橙色“待确认”标记）。\n"
                    "请人工确认或修改标题/单位后再导出。",
                )
            else:
                messagebox.showinfo("OCR 自动命名完成", "全部组的标题/单位已提取，无待确认项。")

        self._start_task("ocrname", work, on_done, title="OCR 自动命名（本地）")

    def direction_analyze(self):
        if self._busy:
            return
        doc = self._active_doc()
        if not doc:
            messagebox.showinfo("识别方向", "请先打开 PDF 文件。")
            return
        candidates = [p for p in range(1, doc.page_count + 1) if p not in doc.separators]
        self._direction_analyze_pages(doc, candidates)

    def direction_analyze_group(self, gi: int, doc=None):
        """分组面板入口：只对某一组内的页面做方向分析（显式传 doc，防切换文档误操作）。"""
        if self._busy:
            return
        doc = doc or self._active_doc()
        if not doc or gi >= len(doc.groups):
            messagebox.showinfo("识别方向", "请先打开 PDF 文件。")
            return
        group = doc.groups[gi]
        candidates = [p for p in group.pages if p not in doc.separators]
        self._direction_analyze_pages(
            doc, candidates, scope=f"文档 {gi + 1}（第 {group.start}-{group.end} 页）")

    def _direction_analyze_pages(self, doc, candidates: list[int], scope: str = ""):
        # 几何建议限定在候选页内（组级分析时不显示组外页面）
        sugg = {p: a for p, a in rotation.orientation_suggestions(doc).items() if p in candidates}
        engine = OcrEngine.get()
        if not engine.available:
            self.log("OCR 不可用，仅按几何尺寸提示方向差异。")
            self._direction_show_results(doc, candidates, sugg, results=[], scope=scope)
            return
        if not candidates:
            self._direction_show_results(doc, candidates, sugg, results=[], scope=scope)
            return

        def work(progress, log, cancel):
            # 所有候选页做 OCR 方向分析（页面尺寸一致不代表内容正放）
            renderer = PageRenderer(doc.path)  # worker 线程内自建自毁
            try:
                return autoname.analyze_directions(
                    doc, renderer, candidates,
                    progress_cb=lambda i, n: progress(i, n, "识别方向"),
                    cancel_cb=cancel,
                )
            finally:
                renderer.close()

        def on_done(results, canceled, error):
            self._task_finish()
            if error:
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"方向分析失败：{tail}")
                messagebox.showerror("识别方向", f"方向分析失败：{tail}")
                return
            if canceled:
                self.log("方向分析已取消。")
                return
            self._direction_show_results(doc, candidates, sugg, results=results, scope=scope)

        if not self._start_task("direction", work, on_done, title="识别方向（本地 OCR 方向对比）"):
            return

    def _direction_show_results(self, doc, candidates, sugg, results, scope: str = ""):
        ocr_pages: list[int] = [
            r.page for r in results
            if r.suggested_angle not in (None, 0)
        ]
        # OCR 判定内容侧放的页面：询问是否一键摆正（可撤销，仍可手动调整）
        if ocr_pages:
            by_page = {r.page: r for r in results}
            desc = "、".join(
                f"第 {p} 页→{by_page[p].suggested_angle}°" for p in ocr_pages
            )
            if messagebox.askyesno(
                "自动摆正",
                f"检测到 {len(ocr_pages)} 页内容侧放（横版表格竖着扫描等）：\n"
                f"{desc}\n\n"
                "是否按建议自动旋转摆正？\n"
                "（仅写入页面旋转属性，不重绘内容；Ctrl+Z 可撤销）",
            ):
                self.apply_rotation(
                    {p: int(by_page[p].suggested_angle) % 360 for p in ocr_pages},
                    doc=doc,
                )
                self.log(f"自动摆正 {len(ocr_pages)} 页：{desc}")
                ocr_pages = []
        pages = sorted(set(sugg) | set(ocr_pages))
        if not pages:
            if results and all(r.suggested_angle is None for r in results):
                messagebox.showwarning(
                    "识别方向",
                    f"方向分析未得到结果：{results[0].suggestion_note or '未知原因'}\n"
                    "请按页面内容人工判断。",
                )
            elif results:
                self.log("方向分析完成：未发现需要旋转的页面。")
            else:
                self.log("未发现几何方向差异的页面。")
            return
        DirectionDialog(self.root, self, doc, pages, results, sugg, scope=scope)

    def apply_rotation(self, mapping: dict[int, int], doc=None):
        doc = doc or self._active_doc()
        if not doc:
            return
        self._push_undo()
        for p, angle in mapping.items():
            if 1 <= p <= doc.page_count:
                doc.pages[p - 1].rotation = int(angle) % 360
        self._refresh_all()
        self.log("已应用旋转：" + "、".join(f"第{p}页→{a}°" for p, a in sorted(mapping.items())))

    def boundary_review_action(self):
        if self._busy:
            return
        doc = self._active_doc()
        if not doc:
            messagebox.showinfo("复核拆分", "请先打开 PDF 文件。")
            return
        engine = OcrEngine.get()
        if not engine.available:
            messagebox.showwarning(
                "复核拆分",
                "该功能依赖本地 OCR，当前不可用。\n请按 README 离线安装 OCR 组件后重试。",
            )
            return

        def work(progress, log, cancel):
            renderer = PageRenderer(doc.path)  # worker 线程内自建自毁
            try:
                return autoname.smart_split_review(
                    doc, renderer, engine,
                    progress_cb=lambda i, n: progress(i, n, "复核拆分"),
                    cancel_cb=cancel,
                )
            finally:
                renderer.close()

        def on_done(statuses, canceled, error):
            self._task_finish()
            if error:
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"复核拆分失败：{tail}")
                messagebox.showerror("复核拆分", f"复核失败：{tail}")
                return
            if canceled:
                self.log("复核拆分已取消，未做任何修改。")
                return
            if not self._doc_still_open(doc):
                self.log(f"{doc.name} 已从工作区移除，复核结果丢弃。")
                return
            # 双重验证一致（页码重置+标题特征）的页面自动补拆——可撤销。
            # 拆分边界记录在页面自身（split_before），内容页不会被排除出导出。
            auto_pages = [s.next_page for s in statuses
                          if s.status == "auto_split" and s.next_page]
            if auto_pages:
                self._push_undo()
                for p in auto_pages:
                    if 2 <= p <= doc.page_count:
                        doc.pages[p - 1].split_before = True
                grouping.refresh_groups(doc)
                self._refresh_all()
                self._log_migrate_notes(doc)
                self.log(f"智能补拆：第 {','.join(map(str, auto_pages))} 页起为新文档"
                         "（页码重置+标题特征双信号一致，Ctrl+Z 可撤销）")
            BoundaryDialog(self.root, self, doc, statuses)

        self._start_task("boundary", work, on_done, title="复核拆分 / 智能补拆（本地 OCR）")

    # -- 页面操作 --------------------------------------------------------------

    def on_page_select(self, page_no: int, preview: bool = True):
        self.current_page = page_no
        if preview:
            doc = self._active_doc()
            if doc:
                self.preview.show(doc, self.renderers[str(doc.path)], page_no)
        self.update_status()

    def step_page(self, delta: int):
        doc = self._active_doc()
        if not doc:
            return
        p = min(max(self.current_page + delta, 1), doc.page_count)
        if p == self.current_page:
            return
        self.current_page = p
        self.sel_pages = {p}
        self.preview.show(doc, self.renderers[str(doc.path)], p)
        self.thumbs.redraw()
        self.update_status()

    def rotate_selection(self, delta: int, absolute: bool = False):
        doc = self._active_doc()
        if not doc:
            return
        pages = sorted(self.sel_pages) if self.sel_pages else [self.current_page]
        self._push_undo()
        for p in pages:
            if 1 <= p <= doc.page_count:
                cur = doc.pages[p - 1].rotation
                doc.pages[p - 1].rotation = (delta % 360) if absolute else (cur + delta) % 360
        self._refresh_all()
        self.log(f"第 {','.join(map(str, pages))} 页旋转 → {doc.pages[pages[0] - 1].rotation}°")

    def rotate_pages_in_group(self, gi: int, delta: int):
        doc = self._active_doc()
        if not doc or gi >= len(doc.groups):
            return
        group = doc.groups[gi]
        self._push_undo()
        for p in group.pages:
            doc.pages[p - 1].rotation = (doc.pages[p - 1].rotation + delta) % 360
        self._refresh_all()
        self.log(f"文档 {gi + 1}（第 {group.start}-{group.end} 页）整组旋转 +{delta}°")

    def _nav_key(self, delta: int):
        if self._editing():
            return
        self.step_page(delta)

    def _delete_key(self):
        if self._editing():
            return
        doc = self._active_doc()
        if not doc:
            return
        pages = sorted(self.sel_pages) if self.sel_pages else [self.current_page]
        pages = [p for p in pages if 1 <= p <= doc.page_count]
        if not pages:
            return
        any_sep = any(doc.pages[p - 1].separator for p in pages)
        self.set_separator(pages, not any_sep)

    # -- 导出 -------------------------------------------------------------------

    def export_all(self):
        if self._busy:
            return
        docs = list(self.ws.documents)
        if not docs:
            messagebox.showinfo("导出", "请先打开 PDF 文件。")
            return
        total_g = sum(len(d.groups) for d in docs)
        if not total_g:
            messagebox.showinfo("导出", "尚未分组。请先运行“自动分组”。")
            return
        out_dir = filedialog.askdirectory(
            title="选择导出目录",
            initialdir=self.settings.get("output_dir") or str(Path.home()),
        )
        if not out_dir:
            return
        template = self.template_var.get() or naming.DEFAULT_TEMPLATE
        opts = exporter.ExportOptions(template=template, out_dir=out_dir, strict=True)
        issues = exporter.preflight(docs, opts)
        if issues:
            # 严格模式（v1.8 默认）：空标题/空单位/待确认字段为阻断项，
            # 仅在用户显式选择"非严格模式"后放行（面向测试文件）。
            if not messagebox.askyesno(
                "严格导出检查未通过",
                "以下分组存在阻断项（空标题、空单位或存在待确认字段）：\n\n"
                + "\n".join(issues[:15])
                + ("\n……" if len(issues) > 15 else "")
                + "\n\n【正式导出】请先补全并确认标题/单位后重试。\n"
                "是否改用【非严格模式】继续导出？（仅建议用于测试文件）",
            ):
                return
            opts.strict = False
        self._busy = True
        self._update_step_state()

        def work(progress, log, cancel):
            # worker 线程：export_documents 自建 pypdf 读写对象，不触碰 Tk/渲染器
            worker_opts = exporter.ExportOptions(
                template=template, out_dir=out_dir, strict=opts.strict,
                on_log=log, on_progress=progress, cancel_check=cancel,
            )
            return exporter.export_documents(docs, worker_opts)

        def on_done(report, canceled, error):
            self._task_finish()
            if error:
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"导出失败：{tail}")
                messagebox.showerror("导出", f"导出过程异常：{tail}\n\n已成功导出的文件保留，源文件未受影响。")
                return
            # 报告由 worker 产生，此处仅读（主线程收尾）
            for doc in docs:
                if self._doc_still_open(doc):
                    for g in doc.groups:
                        if g.unit.strip():
                            self.unit_history.add(g.unit)
            self.settings["output_dir"] = out_dir
            self.settings["template"] = template
            self._save_settings()
            ok, fail = len(report.succeeded), len(report.failed)
            integrity_ok = all(
                report.src_sha_before.get(k) == report.src_sha_after.get(k)
                for k in report.src_sha_before
                if k in report.src_sha_after
            )
            coverage_ok = not report.coverage_issues
            msg = (
                f"导出完成：成功 {ok} 份，失败 {fail} 份。\n"
                f"源文件完整性：{'全部一致（SHA-256 校验通过）' if integrity_ok else '异常！请查看日志'}\n"
                f"页面覆盖率：{'每个有效原页恰好导出一次' if coverage_ok else '异常！存在漏页/重复页，请查看日志'}"
            )
            if canceled or report.canceled:
                done_names = "、".join(r.filename + ".pdf" for r in report.succeeded) or "（无）"
                msg += (
                    "\n（已取消，未导出的组不受影响）\n"
                    f"已验证完成并保留的文件：\n{done_names}"
                )
            self.log(f"导出完成：成功 {ok} 份，失败 {fail} 份。")
            if canceled or report.canceled:
                self.log(f"导出已取消，已完成文件：{done_names}")
            if fail:
                self.log(f"导出失败 {fail} 份，详情见上方日志。")
            ExportDoneDialog(self.root, out_dir, msg)

        self._start_task("export", work, on_done, title="导出")

    # -- 设置 -------------------------------------------------------------------

    def open_settings(self):
        SettingsDialog(self.root, self)

    def _save_settings(self):
        self.settings["template"] = self.template_var.get()
        self.settings["detect_params"] = self.detect_params.to_dict()
        self.settings["unit_history"] = self.unit_history.to_json()
        self.settings["zoom"] = self.preview.zoom
        self.settings["fit_mode"] = self.preview.fit_mode
        try:
            self.settings["window_geometry"] = self.root.geometry()
        except tk.TclError:
            pass
        settings.save_settings(self.settings)

    def on_close(self):
        # 后台任务安全终止：先发取消标志，再限时等待 worker 退出，
        # 避免关窗时 worker 仍在写状态或触碰已销毁的 Tk。
        if self.tasks.busy:
            self.tasks.cancel()
            finished = self.tasks.cancel_and_join(3.0)
            if not finished:
                self.log("后台任务未能在 3 秒内退出，强制关闭窗口。")
        try:
            self._save_settings()
        except Exception:
            pass
        for renderer in self.renderers.values():
            renderer.close()
        self.root.destroy()


# -- 方向识别对话框 -------------------------------------------------------------


class DirectionDialog(tk.Toplevel):
    def __init__(self, master, app: App, doc, pages, results, sugg, scope: str = ""):
        super().__init__(master)
        self.app = app
        self.doc = doc
        title_scope = f" — {scope}" if scope else f" — {doc.name}"
        self.title(f"识别方向{title_scope}")
        self.resizable(False, False)
        ttk.Label(self, text="疑似需要旋转的页面（几何不一致或 OCR 判定内容侧放，常见于横版表格）——请选择旋转角度：",
                  padding=8).pack(anchor="w")
        frame = ttk.Frame(self, padding=6)
        frame.pack(fill="both", expand=True)
        by_page = {r.page: r for r in results}
        ttk.Label(frame, text="页面").grid(row=0, column=0, padx=4, pady=2, sticky="w")
        ttk.Label(frame, text="几何").grid(row=0, column=1, padx=4, pady=2, sticky="w")
        ttk.Label(frame, text="OCR 四方向对比结果").grid(row=0, column=2, padx=4, pady=2, sticky="w")
        self.note_lbls = {}
        for row, p in enumerate(pages, start=1):
            ttk.Label(frame, text=f"第 {p} 页", width=8).grid(row=row, column=0, padx=4, pady=3, sticky="w")
            ttk.Label(frame, text=f"疑似{sugg.get(p, '')}", width=8, foreground="#5d6d7e").grid(
                row=row, column=1, padx=4, pady=3, sticky="w")
            r = by_page.get(p)
            note = r.suggestion_note if r else "（OCR 不可用，请按页面内容判断）"
            lbl = ttk.Label(frame, text=note, width=52, anchor="w", foreground="#5d6d7e")
            lbl.grid(row=row, column=2, padx=4, pady=3, sticky="w")
            self.note_lbls[p] = lbl
            for j, ang in enumerate((90, 180, 270, 0)):
                ttk.Button(frame, text=f"{ang}°", width=4,
                           command=lambda pp=p, a=ang: self._apply(pp, a)).grid(
                    row=row, column=3 + j, padx=1, pady=3)
        btns = ttk.Frame(self)
        btns.pack(pady=6)
        sugg_map = {
            p: int(by_page[p].suggested_angle) % 360
            for p in pages
            if by_page.get(p) and by_page[p].suggested_angle not in (None, 0)
        }
        if sugg_map:
            ttk.Button(btns, text="全部按建议摆正", width=16,
                       command=lambda: self._apply_all(sugg_map)).pack(side="left", padx=4)
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="left", padx=4)

    def _apply(self, page: int, angle: int):
        self.app.apply_rotation({page: angle}, doc=self.doc)
        self.note_lbls[page].configure(text=f"已应用 {angle}°（将写入导出）", foreground="#1e8449")

    def _apply_all(self, sugg_map: dict[int, int]):
        self.app.apply_rotation(sugg_map, doc=self.doc)
        for p, a in sugg_map.items():
            if p in self.note_lbls:
                self.note_lbls[p].configure(text=f"已应用 {a}°（将写入导出）", foreground="#1e8449")


# -- 拆分边界复核对话框 -----------------------------------------------------------


class BoundaryDialog(tk.Toplevel):
    def __init__(self, master, app: App, doc, statuses):
        super().__init__(master)
        self.app = app
        self.doc = doc
        self.title(f"复核拆分边界 — {doc.name}")
        self.resizable(False, False)
        if not statuses:
            ttk.Label(self, text="没有可复核的拆分边界。", padding=12).pack()
            ttk.Button(self, text="关闭", command=self.destroy).pack(pady=6)
            return
        ttk.Label(self, text="空白页拆分是第一依据；OCR 首页标题检测作为第二验证：",
                  padding=8).pack(anchor="w")
        frame = ttk.Frame(self, padding=6)
        frame.pack(fill="both", expand=True)
        self.action_lbls = {}
        for row, s in enumerate(statuses):
            if s.status == "ok":
                icon, fg = "✓", "#1e8449"
            elif s.status == "warn":
                icon, fg = "⚠", "#d68910"
            elif s.status == "auto_split":
                icon, fg = "✓", "#1e8449"
            else:
                icon, fg = "＋", "#2e86c1"
            ttk.Label(frame, text=icon, width=2, foreground=fg).grid(row=row, column=0, padx=2, pady=3)
            ttk.Label(frame, text=s.note, width=72, anchor="w").grid(row=row, column=1, padx=2, pady=3, sticky="w")
            lbl = ttk.Label(frame, text="", width=14)
            lbl.grid(row=row, column=2, padx=2, pady=3)
            if s.status == "suggest_split":
                btn = ttk.Button(frame, text="在此拆分", width=10,
                                 command=lambda p=s.next_page, l=lbl: self._split(p, l))
                btn.grid(row=row, column=3, padx=2, pady=3)
                self.action_lbls[id(lbl)] = lbl
            elif s.status == "auto_split":
                lbl.configure(text="已自动补拆", foreground="#1e8449")
        ttk.Button(self, text="关闭", command=self.destroy).pack(pady=6)

    def _split(self, page_no: int, lbl):
        self.app.add_split_before(page_no, doc=self.doc)
        lbl.configure(text="已拆分", foreground="#1e8449")


# -- 设置对话框 -----------------------------------------------------------------


class SettingsDialog(tk.Toplevel):
    PARAMS = [
        ("ink_threshold_pct", "判空白·墨迹比上限（%）", 0.05, 3.0, 0.05),
        ("maybe_ink_max_pct", "疑似区·墨迹比上限（%）", 0.2, 5.0, 0.1),
        ("dark_pct_blank", "判空白·深色比上限（%）", 0.005, 0.2, 0.005),
        ("dark_pct_content", "判内容·深色比下限（%）", 0.02, 0.5, 0.01),
        ("bbox_frac_content", "判内容·内容包围盒面积比", 0.01, 0.3, 0.01),
        ("grad_blank_max_pct", "判空白·梯度比上限（%）", 0.05, 2.0, 0.05),
        ("grad_content_min_pct", "判内容·梯度比下限（%）", 0.1, 3.0, 0.1),
        ("adaptive_offset", "自适应阈值偏移（均值−偏移）", 10.0, 100.0, 1.0),
    ]

    def __init__(self, master, app: App):
        super().__init__(master)
        self.app = app
        self.title("设置")
        self.resizable(False, False)
        self.transient(master)
        self.vars: dict[str, tk.DoubleVar] = {}
        self.val_lbls: dict[str, ttk.Label] = {}

        f = ttk.LabelFrame(self, text="空白检测阈值（修改后需重新运行“检测空白页”）", padding=8)
        f.pack(fill="x", padx=8, pady=8)
        for i, (key, label, lo, hi, step) in enumerate(self.PARAMS):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            var = tk.DoubleVar(value=float(getattr(app.detect_params, key)))
            self.vars[key] = var
            ttk.Scale(f, from_=lo, to=hi, variable=var, length=250,
                      command=lambda _v, k=key: self._fmt(k)).grid(row=i, column=1, sticky="we", padx=4)
            self.val_lbls[key] = ttk.Label(f, text="", width=7)
            self.val_lbls[key].grid(row=i, column=2, padx=4)
            self._fmt(key)

        g = ttk.LabelFrame(self, text="命名与导出", padding=8)
        g.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(g, text="文件名模板").grid(row=0, column=0, sticky="w", padx=4)
        self.template_var = tk.StringVar(value=app.template_var.get())
        ttk.Entry(g, textvariable=self.template_var, width=40).grid(row=0, column=1, padx=4)
        ttk.Label(g, text="可用变量：{title} {unit} {index} {orig} {start} {end}",
                  foreground="#5d6d7e").grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(g, text="默认输出目录").grid(row=2, column=0, sticky="w", padx=4, pady=(4, 0))
        self.out_var = tk.StringVar(value=app.settings.get("output_dir", ""))
        ttk.Entry(g, textvariable=self.out_var, width=40).grid(row=2, column=1, padx=4, pady=(4, 0))
        ttk.Button(g, text="浏览…", command=self._browse_out).grid(row=2, column=2, padx=4, pady=(4, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="恢复默认", command=self._reset).pack(side="left")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right", padx=4)

    def _fmt(self, key: str):
        self.val_lbls[key].configure(text=f"{self.vars[key].get():.3g}")

    def _browse_out(self):
        d = filedialog.askdirectory(title="选择默认输出目录")
        if d:
            self.out_var.set(d)

    def _reset(self):
        defaults = blankdetect.DetectParams()
        for key, var in self.vars.items():
            var.set(float(getattr(defaults, key)))
            self._fmt(key)
        self.template_var.set(naming.DEFAULT_TEMPLATE)

    def _ok(self):
        for key, var in self.vars.items():
            setattr(self.app.detect_params, key, float(var.get()))
        self.app.template_var.set(self.template_var.get().strip() or naming.DEFAULT_TEMPLATE)
        out = self.out_var.get().strip()
        if out:
            self.app.settings["output_dir"] = out
        self.app._save_settings()
        self.app.log("设置已保存。新阈值将在下次“检测空白页”时生效。")
        self.destroy()


class ExportDoneDialog(tk.Toplevel):
    """导出完成结果：附“打开输出目录”一键入口（Windows 资源管理器）。"""

    def __init__(self, master, out_dir: str, msg: str):
        super().__init__(master)
        self.out_dir = out_dir
        self.title("导出完成")
        self.resizable(False, False)
        self.transient(master)
        ttk.Label(self, text=msg, padding=16, wraplength=440, justify="left").pack(anchor="w")
        btns = ttk.Frame(self, padding=(16, 0, 16, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="打开输出目录", command=self._open).pack(side="left")
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")

    def _open(self):
        try:
            os.startfile(self.out_dir)  # Windows 专属：资源管理器打开目录
        except Exception:
            pass
