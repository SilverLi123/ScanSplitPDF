"""主界面：导入 → 自动检测与分组 → 复核拆分/补拆 → OCR 命名 → 校对 → 无损导出。

工作流原则（与核心模块一致）：
- 视觉空白检测（blankdetect）生成分隔候选，本身只回答"这页看起来是不是空白"；
- 业务属性由上下文判定（splitctx）依据印刷页码、首页结构（标题/主送机关）、
  续页标记与前后内容页标题关系区分：separator / internal_blank / uncertain；
- 只有 separator 允许作为分隔空白排除；internal_blank 与 uncertain 一律默认保留
  （不静默丢页）；证据不足或互相矛盾时降级为待确认，交人工/复核；
- 无空白页的文件由"智能补拆"（autoname.smart_split_review）产生自动建议或
  待确认边界（页码重置 + 标题特征双信号一致才自动补拆，可撤销）；
- 页面方向采用统一三来源模型（PageState.effective_rotation）：人工覆盖 ??
  自动检测建议 ?? 0，预览 / OCR / 命名 / 导出全部只读它；自动检测只写
  detected_rotation，绝不覆盖人工结果，原 PDF 的 /Rotate 不二次旋转；
- 人工修改优先：人工改过的分隔标记、已确认的标题/单位字段，自动结果永不覆盖；
- 旋转只设置目标 /Rotate，导出绝不重绘页面；OCR 全程本地离线运行，
  识别结果只作建议，可人工修改。
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import autoname, blankdetect, exporter, grouping, loader, naming, rotation, settings, splitctx, pipeline, workflow, presentation
from .review_ui import ReviewDialog
from .loader import LoadError
from .model import ScanDocument, Workspace
from .ocr import OcrEngine
from .render import PageRenderer, RenderError
from .tasks import TaskManager
from .ui_widgets import (
    ExportSummaryDialog,
    CheckCenter,
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
        self._review_dialog = None
        self._log_visible = False
        self._precheck = None
        self._last_export = None
        self._preview_errors = {}
        self._operation_notice = None
        # v1.9 后台任务：单工作线程 + Queue/root.after 回传（UI 不冻结）
        self.tasks = TaskManager(root, self.log)
        self.tasks.progress_hook = self._on_task_progress
        self._prog = None

        self._build_ui()
        self.template_var.trace_add("write", self._on_template_change)
        try:
            self.preview.zoom = min(
                max(float(self.settings.get("zoom", 1.0)), PreviewPane.MIN_ZOOM),
                PreviewPane.MAX_ZOOM,
            )
        except (TypeError, ValueError):
            self.preview.zoom = 1.0
        self.preview.fit_mode = bool(self.settings.get("fit_mode", True))
        self._refresh_all()
        enable_drag_drop(self.root, self.import_files_async)
        self.log("就绪。请打开 PDF 文件（可多选或直接拖入窗口）。")

    # -- 界面构建 -----------------------------------------------------------

    def _build_ui(self):
        root = self.root
        root.title("ScanSplit PDF — 扫描件拆分与命名工具")

        style = ttk.Style(root)
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 8))
        style.configure("Step.TLabel", font=("Microsoft YaHei UI", 10), padding=(12, 6))
        steps = ttk.Frame(root, padding=(8, 4))
        steps.pack(fill="x")
        self.step_labels = []
        self.step_names = ("导入", "自动分析", "检查拆分", "确认名称", "导出")
        for i,text in enumerate(self.step_names):
            label = ttk.Label(steps, text=text, style="Step.TLabel")
            label.grid(row=0,column=i,sticky="ew")
            steps.columnconfigure(i,weight=1,uniform="steps")
            self.step_labels.append(label)
        steps.bind("<Configure>",lambda e:[label.configure(wraplength=max(120,e.width//5-28)) for label in self.step_labels])

        guide = ttk.Frame(root, padding=(18, 8, 18, 12))
        guide.pack(fill="x")
        self.next_button = ttk.Button(guide, style="Primary.TButton", command=self.workflow_next)
        self.next_button.pack(side="right", padx=(16, 0))
        self.workflow_title = tk.StringVar()
        self.workflow_detail = tk.StringVar()
        title_label = ttk.Label(guide, textvariable=self.workflow_title, font=("Microsoft YaHei UI", 12, "bold"))
        title_label.pack(anchor="w")
        detail_label = ttk.Label(guide, textvariable=self.workflow_detail, foreground="#49566a", wraplength=800)
        detail_label.pack(anchor="w", pady=(4, 0))
        guide.bind("<Configure>", lambda e: [label.configure(wraplength=max(300, e.width-self.next_button.winfo_reqwidth()-60)) for label in (title_label,detail_label)])
        tools = ttk.Frame(root, padding=(8, 4))
        tools.pack(fill="x")
        self.tool_btns: dict[str, ttk.Button] = {}
        for key, text, cmd in [
            ("open", "添加 PDF…", self.open_dialog),
            ("detect", "重新自动拆分", self.automatic_split),
            ("group", "查看 / 调整拆分", self.review_pending),
            ("ocr", "重新识别名称", self.ocr_name),
        ]:
            b = ttk.Button(tools, text=text, command=cmd)
            b.pack(side="left", padx=3)
            self.tool_btns[key] = b
        self.tool_btns["review"] = self.tool_btns["group"]
        more = ttk.Menubutton(tools, text="更多操作")
        menu = tk.Menu(more, tearoff=False)
        for text, cmd in [("撤销上一步", self.undo), ("重做", self.redo),
                          ("重新检查方向…", self.direction_analyze),
                          ("设置命名格式…", self.open_settings),
                          ("使用帮助", self.show_help), ("显示 / 隐藏处理记录", self.toggle_log)]:
            menu.add_command(label=text, command=cmd)
        more.configure(menu=menu)
        more.pack(side="right", padx=4)
        self.more_button = more

        paned = ttk.PanedWindow(root, orient="horizontal", height=250)
        paned.pack(fill="both", expand=True, padx=4, pady=2)

        left = ttk.Frame(paned)
        files_frame = ttk.LabelFrame(left, text="已导入文件", padding=4)
        files_frame.pack(fill="x", padx=2)
        self.file_list = tk.Listbox(files_frame, height=4, exportselection=False)
        self.file_list.pack(fill="x")
        file_scroll = ttk.Scrollbar(files_frame, orient="horizontal", command=self.file_list.xview)
        self.file_list.configure(xscrollcommand=file_scroll.set)
        file_scroll.pack(fill="x")
        self.selected_file_var = tk.StringVar(value="尚未导入文件")
        selected_file_label = ttk.Label(files_frame, textvariable=self.selected_file_var, wraplength=280, justify="left")
        selected_file_label.pack(fill="x", pady=(3,0))
        files_frame.bind("<Configure>", lambda e: selected_file_label.configure(wraplength=max(120,e.width-16)))
        self.file_list.bind("<<ListboxSelect>>", self._on_file_select)
        self.file_list.bind("<Button-3>", self._on_file_menu)
        ttk.Label(left, text="原始页面 · 点击预览").pack(anchor="w", padx=4, pady=(4, 0))
        self.thumbs = ThumbnailStrip(left, self)
        self.thumbs.pack(fill="both", expand=True, padx=2, pady=(0, 4))

        self.preview = PreviewPane(paned, self)

        right = ttk.LabelFrame(paned, text="整理工作区", padding=2, width=400)
        self.right_panel = right
        self.review_tabs = ttk.Notebook(right)
        self.review_tabs.pack(fill="both",expand=True)
        self.check_center = CheckCenter(self.review_tabs,self)
        self.review_tabs.add(self.check_center,text="检查中心")
        name_tab=ttk.Frame(self.review_tabs)
        self.review_tabs.add(name_tab,text="文件名称")
        self.name_tab=name_tab
        self.groups = GroupPanelArea(name_tab, self)
        self.groups.pack(fill="both", expand=True)

        paned.add(left, weight=3)
        paned.add(self.preview, weight=4)
        paned.add(right, weight=5)
        self.main_panes = paned
        self._pane_width = None
        def size_panes(event):
            if event.width == self._pane_width:
                return
            self._pane_width = event.width
            left_width = max(260, int(event.width*.24))
            right_width = max(480, int(event.width*.44))
            paned.sashpos(0, left_width)
            paned.sashpos(1, max(left_width+300, event.width-right_width))
        paned.bind("<Configure>", size_panes)

        bottom = ttk.Frame(root, padding=(12, 6, 12, 8))
        bottom.pack(fill="x")
        export_row = ttk.Frame(bottom)
        export_row.pack(fill="x")
        self.export_button = ttk.Button(export_row, text="导出 PDF…", style="Primary.TButton", command=self.export_all)
        self.export_button.pack(side="right", padx=(16,0))
        self.tool_btns["export"] = self.export_button
        export_info = ttk.Frame(export_row)
        export_info.pack(side="left",fill="x",expand=True)
        self.export_notice_var = tk.StringVar()
        notice = ttk.Label(export_info,textvariable=self.export_notice_var,font=("Microsoft YaHei UI",10,"bold"),wraplength=800)
        notice.pack(anchor="w")
        status_row = ttk.Frame(bottom)
        status_row.pack(fill="x",pady=(4,0))
        self.status_var = tk.StringVar()
        ttk.Label(status_row, textvariable=self.status_var, anchor="w").pack(
            side="left", fill="x", expand=True)
        self.hint_var = tk.StringVar()
        ttk.Label(status_row, textvariable=self.hint_var, foreground="#1a5276",
                  font=("Segoe UI", 9, "bold")).pack(side="right")
        self.log_panel = LogPanel(bottom)

        root.bind("<Control-o>", lambda e: self.open_dialog())
        root.bind("<Control-z>", lambda e: self.undo(from_key=True))
        root.bind("<Control-y>", lambda e: self.redo(from_key=True))
        root.bind("<Control-e>", lambda e: self.export_all())
        root.bind("<Left>", lambda e: self._nav_key(-1))
        root.bind("<Right>", lambda e: self._nav_key(1))
        root.bind("<Delete>", lambda e: self._delete_key())

    def toggle_log(self):
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.log_panel.pack(fill="x", pady=(2, 0))
        else:
            self.log_panel.pack_forget()

    def show_help(self):
        messagebox.showinfo("如何整理扫描 PDF", "按顶部的大按钮往下走：\n\n"
            "1. 选择 PDF → 开始自动拆分。\n"
            "2. 检查窗口会先让你把页面转正，再检查文件交界。\n"
            "   左右页属于不同文件：选“这里开始新文件”。\n"
            "   是续页或附件：选“属于上一份”。\n"
            "   空白页默认保留，只有明确确认才排除。\n"
            "3. 拆分确认后识别名称，右侧修改标题和填报单位。\n"
            "4. 核对导出清单，选择保存位置。\n\n"
            "暂时拿不准可稍后处理；待确认结果无法正式导出。人工决定可撤销，源文件保持不变。")

    def activate_document(self, index):
        self.doc_index = index
        self.current_page, self.sel_pages = 1, {1}
        self._refresh_all()

    def workflow_next(self):
        if self._busy:
            return
        step = workflow.next_step(self.ws.documents)
        if self.ws.documents and step.action not in ("analyze", "name", "export"):
            self.activate_document(step.doc_index)
        if step.action == "open":
            self.open_dialog()
        elif step.action in ("analyze", "reanalyze"):
            self.automatic_split(guided=True)
        elif step.action == "review":
            self.review_pending()
        elif step.action == "name":
            self.ocr_name()
        elif step.action == "names":
            self.focus_next_name()
        else:
            self.export_all()

    def focus_next_name(self):
        self.review_tabs.select(self.name_tab)
        for i, doc in enumerate(self.ws.documents):
            for gi, group in enumerate(doc.groups):
                if not group.title_confirmed or not group.unit_confirmed or not group.title.strip() or not group.unit.strip():
                    if self._active_doc() is not doc:
                        self.activate_document(i)
                    self.groups.panels[gi].focus_pending()
                    return

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
            f"已导入 {len(docs)} 个 PDF · 共 {total_pages} 个原始页{cur}"
        )
        self.selected_file_var.set(doc.name if doc else "尚未导入文件")
        confirmed = sum(presentation.name_ready(d,g) for d in docs for g in d.groups)
        if not n_groups:
            notice = "完成拆分与命名后，从右侧“导出 PDF…”保存文件。"
        elif confirmed < n_groups:
            notice = f"整理就绪 {confirmed}/{n_groups} 份，还有 {n_groups-confirmed} 份待检查。点击“导出 PDF…”可查看清单。"
        else:
            notice = f"{n_groups} 份文件整理就绪，点击右侧“导出 PDF…”核对清单并保存。"
        self.export_notice_var.set(notice)
        self._update_step_state(len(docs), self._blank_analysis_done(), n_groups, n_conf)
        self._refresh_checks()

    def _check_key(self):
        return presentation.state_key(self.ws.documents), self.template_var.get()

    def _on_template_change(self, *args):
        for panel in self.groups.panels:
            panel._update_filename()
        self.update_status()

    def _refresh_checks(self):
        docs=self.ws.documents
        key=self._check_key()
        items=presentation.issues(docs)
        text="页面完整性：尚未执行导出前检查；保真校验将在导出时执行。"
        if self._precheck:
            if self._precheck[0]==key:
                items += self._precheck[1]
                text="导出前完整性预检："+("存在阻断问题" if self._precheck[1] else "通过；输出保真尚待导出时验证")
            else:
                text="文件状态已变化，导出前需要重新检查。"
        exported=0
        if self._last_export and self._last_export[0]==key:
            feedback=self._last_export[1]
            exported=feedback['ok']
            text="\n".join(feedback['lines'][:3])
            items += presentation.precheck_issues(docs,feedback['errors'],prefix="result")
        items += list(self._preview_errors.values())
        if self._operation_notice and self._operation_notice.doc_index < len(docs):
            items.append(self._operation_notice)
        ready=sum(presentation.name_ready(d,g) for d in docs for g in d.groups)
        total=sum(len(d.groups) for d in docs)
        self.check_center.set_content(items,ready,total,text)
        self.review_tabs.tab(self.check_center,text=f"检查中心（{len(items)}）" if items else "检查中心")
        counts=presentation.step_counts(docs,exported)
        stage=workflow.next_step(docs).stage
        for i,(done,count,unit) in enumerate(counts):
            marker="●" if i==stage else "✓" if count and done==count else "○"
            self.step_labels[i].configure(text=f"{marker} {i+1} {self.step_names[i]}\n{done}/{count}{unit}完成 · 待{max(0,count-done)}")

    def navigate_issue(self, issue, process=False):
        if self._busy or not 0<=issue.doc_index<len(self.ws.documents):
            return
        doc=self.ws.documents[issue.doc_index]
        self.locate_page(issue.page,doc)
        if issue.action=="field" and issue.group_index is not None:
            if not presentation.split_ready(doc,doc.groups[issue.group_index]):
                if process:
                    self.review_pending()
                return
            self.review_tabs.select(self.name_tab)
            self.groups.panels[issue.group_index].focus_pending(issue.field)
        elif process:
            if issue.action in ("analyze","reanalyze"):
                self.automatic_split(guided=True,only_doc=doc)
            elif issue.action=="name":
                self.ocr_name()
            elif issue.action in ("direction","boundary","group"):
                self.review_pending()
                dialog=self._review_dialog
                iid=f"d:{issue.page}" if issue.action=="direction" else f"g:{issue.group_index}" if issue.action=="group" else str(issue.page)
                if dialog and dialog.tree.exists(iid):
                    dialog.tree.selection_set(iid)
                    dialog.tree.see(iid)
                    dialog.selected()
            else:
                messagebox.showerror("问题详情",issue.reason)

    def report_preview_error(self,doc,page,error):
        di=next((i for i,d in enumerate(self.ws.documents) if d is doc),None)
        if di is not None:
            key=f"preview:{di}:{page}"
            self._preview_errors[key]=presentation.Issue(key,"页面预览","页面预览失败",str(error),di,page,severity="error")

    def clear_preview_error(self,doc,page):
        di=next((i for i,d in enumerate(self.ws.documents) if d is doc),None)
        self._preview_errors.pop(f"preview:{di}:{page}",None)

    def record_operation_notice(self,title,reason,action="analyze",doc=None,canceled=False):
        doc=doc or self._active_doc()
        di=next((i for i,d in enumerate(self.ws.documents) if d is doc),None)
        if di is not None:
            self._operation_notice=presentation.Issue("operation","任务状态",title,reason,di,action=action,severity="info" if canceled else "error")
            self.update_status()

    def _blank_analysis_done(self) -> bool:
        """是否已对全部文档执行过空白检测。

        关键区分（v1.9.1 P0-B）：“检测完成但无空白候选”与“尚未检测”是两种状态，
        旧代码用 `blanks + maybes > 0` 判定，导致无空白页的文件永远无法进入分组。
        改判"是否存在已检测过的文档"（至少一页 blank 结果非 None）。
        """
        return bool(self.ws.documents) and all(p.blank is not None for d in self.ws.documents for p in d.pages)

    def _update_step_state(
        self,
        n_docs: int | None = None,
        has_blank_analysis: bool | None = None,
        n_groups: int | None = None,
        n_conf: int | None = None,
    ):
        """按工作流阶段启用/置灰工具栏按钮，并在状态栏给出“下一步”提示。
        参数缺省时从工作区现算（后台任务收尾等场景直接调用）。

        has_blank_analysis 语义 = “是否已执行过空白检测”（而非“是否检测到空白页”）：
        无空白候选的文件同样允许进入分组/补拆，符合连续件的工作流。
        """
        if n_docs is None:
            docs = self.ws.documents
            n_docs = len(docs)
            has_blank_analysis = self._blank_analysis_done()
            n_groups = sum(len(d.groups) for d in docs)
            n_conf = sum(1 for d in docs for g in d.groups if g.need_confirm)
        docs = self.ws.documents
        boundaries_ready = bool(docs) and all(d.groups and not d.pending_boundaries
            and not d.analysis_stale and not any(p.has_rotation_proposal for p in d.pages)
            and all(g.boundary_confirmed for g in d.groups) for d in docs)
        readiness = {
            "open": True,
            "detect": n_docs > 0,
            "group": n_docs > 0 and has_blank_analysis,
            "ocr": boundaries_ready,
            "review": n_docs > 0 and has_blank_analysis,
            "export": n_docs > 0 and n_groups > 0,
        }
        for key, ready in readiness.items():
            btn = self.tool_btns.get(key)
            if btn is not None:
                btn.state(["!disabled"] if ready and not self._busy else ["disabled"])
        def set_edit_state(widget):
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="disabled" if self._busy else "readonly")
            elif isinstance(widget, ttk.Entry):
                widget.configure(state="disabled" if self._busy else "normal")
            for child in widget.winfo_children():
                set_edit_state(child)
        if hasattr(self, "groups"):
            set_edit_state(self.groups)
            self.groups.next_btn.state(["!disabled"] if docs and not self._busy else ["disabled"])
            self.groups.rename_btn.state(["!disabled"] if boundaries_ready and not self._busy else ["disabled"])
            self.groups.set_current(self.groups.current_group)
        step = workflow.next_step(docs)
        self.hint_var.set("下一步：" + step.button + ("（自动拆分草稿）" if step.action=="analyze" else ""))
        self.workflow_title.set("正在处理，请稍候…" if self._busy else step.title)
        self.workflow_detail.set("可在进度窗口取消。完成后会继续提示下一步。" if self._busy else step.detail)
        self.next_button.configure(text=step.button)
        self.next_button.state(["disabled"] if self._busy else ["!disabled"])
        if self._busy:
            self.check_center.process_button.state(["disabled"])
        self.more_button.state(["disabled"] if self._busy else ["!disabled"])
        for i, label in enumerate(self.step_labels):
            label.configure(foreground="#155cad" if i == step.stage else "#596579",
                            font=("Microsoft YaHei UI", 10, "bold" if i == step.stage else "normal"))

    def _has_blank_candidates(self) -> bool:
        return any(
            p.blank and p.blank.verdict in ("blank", "maybe")
            for d in self.ws.documents for p in d.pages
        )

    def _refresh_all(self):
        self._rebuild_file_list()
        doc = self._active_doc()
        for path, renderer in self.renderers.items():
            if not doc or path != str(doc.path):
                renderer.clear_cache()
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
            self.workflow_title.set(f"{title or '正在处理'} · {done}/{total}")
            self.workflow_detail.set(f"{phase or '正在本地处理'}。可在进度窗口取消；尚未确认的结果不会正式导出。")
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
        # A tiny/cached job can finish during start()'s first queue poll.
        # Set busy before dispatch so its completion cannot be overwritten.
        self._busy = True
        self._operation_notice = None
        if not self.tasks.start(kind, work, on_done, title=title):
            self._busy = self.tasks.busy
            messagebox.showinfo("请稍候", "当前已有任务在后台运行，请等待完成或先取消。")
            return False
        self._update_step_state()
        self._refresh_checks()
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
            self.import_files_async(list(paths))

    def import_files_async(self, paths):
        if self._busy:
            return
        existing = {d.path.resolve() for d in self.ws.documents}
        def work(progress, log, cancel):
            loaded, errors = [], []
            for i, value in enumerate(paths, 1):
                if cancel():
                    break
                path = Path(value).resolve()
                progress(i-1, len(paths), f"导入 {path.name}")
                if path.suffix.lower() != ".pdf" or path in existing:
                    continue
                try:
                    reader, warnings = loader.open_document(path)
                    doc = ScanDocument(path, reader, warnings)
                    grouping.refresh_groups(doc)
                    for g in doc.groups:
                        g.boundary_confirmed = False
                    loaded.append(doc)
                    existing.add(path)
                except (LoadError, OSError) as exc:
                    errors.append(str(exc))
            return loaded, errors
        def done(result, canceled, error):
            self._task_finish()
            if canceled:
                self.log("导入已取消，工作区保持原状。")
                return
            if error:
                messagebox.showerror("导入", error.splitlines()[-1])
                return
            loaded, errors = result
            for doc in loaded:
                try:
                    renderer = PageRenderer(doc.path)  # owned by main thread
                except RenderError as exc:
                    errors.append(str(exc))
                    continue
                self.ws.documents.append(doc)
                self.renderers[str(doc.path)] = renderer
                self.log(f"导入 {doc.name}（{doc.page_count} 页）")
                for warning in doc.warnings:
                    self.log(f"警告：{warning}")
            if errors:
                messagebox.showwarning("导入", "\n".join(errors[:8]))
            if loaded:
                self.doc_index = max(0, len(self.ws.documents)-1)
                self.current_page, self.sel_pages = 1, {1}
            self._refresh_all()
        self._start_task("import", work, done, title="导入 PDF")

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
            for g in doc.groups:
                g.boundary_confirmed = False
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
        self._preview_errors.clear()
        self._operation_notice = None
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
            if n_blank + n_maybe == 0:
                messagebox.showinfo(
                    "检测完成",
                    "本文件未检测到空白页。\n\n"
                    "如为连续件，可点击“自动分组”建立初始分组，"
                    "再用“复核拆分/补拆”按页码重置与标题特征提出拆分建议。",
                )
            else:
                messagebox.showinfo(
                    "检测完成",
                    f"空白候选 {n_blank} 页（蓝色“空白”徽章），疑似空白 {n_maybe} 页（橙色“疑似”徽章）。\n\n"
                    "下一步：点击“自动分组”，系统将结合前后页上下文判定这些空白页是"
                    "“分隔页”还是“内部空白”（内部空白默认保留，不丢页）；\n"
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
                    pipeline.update_boundary_state(d)
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
            if self._has_blank_candidates():
                messagebox.showinfo(
                    "自动分组完成",
                    "已结合前后页上下文识别分隔点：内部空白与待确认页均保留在文档内。" + tail +
                    "\n可在缩略图上右键调整分隔页；建议点击“复核拆分”做双重验证。",
                )
            else:
                messagebox.showinfo(
                    "自动分组完成",
                    "本文件未检测到空白页，已建立初始分组（整个文件为一份）。\n\n"
                    "连续件请点击“复核拆分/补拆”，系统将按印刷页码重置与标题特征"
                    "提出拆分建议；确认后可一键补拆。",
                )

        self._start_task("autogroup", work, on_done, title="自动分组（上下文判定）")

    def automatic_split(self, guided=False, only_doc=None, on_complete=None):
        if self._busy or not self.ws.documents:
            return
        docs = [only_doc] if only_doc else list(self.ws.documents)
        self._push_undo()
        snap = self.ws.snapshot()

        def work(progress, log, cancel):
            engine = OcrEngine.get()  # initialise models on worker, not Tk thread
            for doc in docs:
                if cancel():
                    break
                pipeline.analyze_document(doc, self.detect_params, engine, progress, cancel)
                log(f"{doc.name}：{len(doc.groups)} 份，{len(doc.pending_boundaries)} 处边界待确认")

        def on_done(result, canceled, error):
            self._task_finish()
            if canceled or error:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                self.log("自动拆分已回滚：" + (error.splitlines()[-1] if error else "用户取消"))
                self.record_operation_notice("自动分析失败" if error else "自动分析已取消",(error.splitlines()[-1] if error else "处理前的草稿和人工决定已保留，可以重新开始。"),doc=docs[0],canceled=not error)
                return
            self._refresh_all()
            if on_complete:
                on_complete()
            elif guided:
                step = workflow.next_step(self.ws.documents)
                self.activate_document(step.doc_index)
                self.review_pending()
            else:
                self.log("拆分草稿已更新。点击顶部主要操作，继续检查方向和拆分。")
        self._start_task("automatic", work, on_done, title="自动拆分")

    def review_pending(self):
        doc = self._active_doc()
        if doc and not self._busy:
            if self._review_dialog and self._review_dialog.winfo_exists():
                if self._review_dialog.doc is doc:
                    self._review_dialog.lift()
                    return
                self._review_dialog.close()
            self._review_dialog = ReviewDialog(self.root, self, doc)

    def resolve_boundary(self, page_no, split, doc=None):
        doc = doc or self._active_doc()
        if not doc or self._busy:
            return
        self._push_undo()
        doc.manual_boundaries[page_no] = bool(split)
        doc.pending_boundaries.pop(page_no, None)
        if page_no > 1:
            doc.pages[page_no - 1].split_before = bool(split)
        # A manual merge also cancels exclusion in the preceding blank run.
        if not split:
            p = page_no - 1
            while p >= 1 and doc.pages[p-1].blank and doc.pages[p-1].blank.verdict == "blank":
                doc.pages[p-1].separator = False
                doc.manual_separator_pages.add(p)
                p -= 1
        grouping.refresh_groups(doc)
        pipeline.update_boundary_state(doc)
        self._refresh_all()

    def exclude_confirmed_blank(self, page_no, doc):
        if self._busy:
            return
        p = page_no if doc.pages[page_no-1].blank and doc.pages[page_no-1].blank.verdict == "blank" else page_no-1
        run = []
        while p >= 1 and doc.pages[p-1].blank and doc.pages[p-1].blank.verdict == "blank":
            run.append(p)
            p -= 1
        if not run:
            messagebox.showinfo("确认分隔空白", "这处边界前没有检测到空白页；请选择保留页面并拆开。")
            return
        if not messagebox.askyesno("排除分隔空白", f"确认第 {min(run)}-{max(run)} 页是分隔空白，导出时排除这些页？\n源文件保留，可撤销。"):
            return
        self._push_undo()
        for p in run:
            doc.pages[p-1].separator = True
            doc.manual_separator_pages.add(p)
            doc.pending_boundaries.pop(p, None)
        next_page = max(run)+1
        if next_page <= doc.page_count:
            doc.manual_boundaries[next_page] = True
            doc.pages[next_page-1].split_before = True
            doc.pending_boundaries.pop(next_page, None)
        doc.pending_boundaries.pop(page_no, None)
        grouping.refresh_groups(doc)
        pipeline.update_boundary_state(doc)
        self._refresh_all()

    def confirm_group_boundary(self, doc, gi):
        if self._busy:
            return
        g = doc.groups[gi]
        if doc.analysis_stale:
            self.review_pending()
            return
        if any(g.start <= p <= g.end + 1 for p in doc.pending_boundaries):
            self.review_pending()
            return
        if any(p.has_rotation_proposal for p in doc.pages[g.start-1:g.end]):
            self.show_direction_proposals(doc)
            return
        self._push_undo()
        g.boundary_confirmed = True
        for p in g.pages:
            doc.manual_boundaries[p] = p == g.start and p > 1
            doc.manual_separator_pages.add(p)  # group confirmation also confirms retained blanks
        self._refresh_all()

    def show_direction_proposals(self, doc):
        pages = [p.src_index for p in doc.pages if p.has_rotation_proposal]
        results = [autoname.DirectionResult(p, {}, doc.pages[p-1].rotation_proposal,
                   doc.pages[p-1].rotation_proposal_note,
                   doc.pages[p-1].rotation_proposal_confidence) for p in pages]
        DirectionDialog(self.root, self, doc, pages, results, {}, "待确认")

    def _log_migrate_notes(self, doc):
        """转述 refresh_groups 的迁移/歧义说明（v1.8：人工编辑不静默丢失）。"""
        for note in getattr(doc, "group_migrate_notes", None) or []:
            self.log(f"分组调整：{note}")

    def rename_affected_groups(self, doc=None):
        """重新识别"拆分边界调整后受影响的分组"（v1.9.1 P0-C）。

        只对**待确认**的组重跑 OCR 命名，且 preserve_confirmed=True：
        人工已确认的标题/单位字段绝不被覆盖，只填补空白或歧义待确认的字段。
        这样"命名后改边界"不会把旧文件名静默迁移到错误的新分组，
        用户也有明确的入口重新识别受影响分组。
        """
        if self._busy:
            return
        doc = doc or self._active_doc()
        if not doc or not doc.groups:
            messagebox.showinfo("重新识别", "请先打开并分组 PDF 文件。")
            return
        # 精确范围（v1.9.1 改进6）：refresh_groups 回传的受影响组
        # ∪ 当前仍待确认的组（避免遗漏人工标待确认但未被判"受影响"的组）。
        affected = set(getattr(doc, "affected_group_indices", []) or [])
        targets = affected | {gi for gi, g in enumerate(doc.groups) if g.need_confirm}
        targets = {gi for gi in targets if 0 <= gi < len(doc.groups)}
        if not targets:
            messagebox.showinfo("重新识别", "当前没有受影响或待确认的分组，无需重新识别。")
            return
        engine = OcrEngine.get()
        use_ocr = engine.available
        self._push_undo()
        snap = self.ws.snapshot()

        def work(progress, log, cancel):
            renderer = PageRenderer(doc.path) if use_ocr else None
            try:
                autoname.auto_name_groups(
                    doc, renderer, engine=engine if use_ocr else None,
                    progress_cb=lambda i, n: progress(i, n, "重新识别"),
                    cancel_cb=cancel,
                    only_indices=targets, preserve_confirmed=True,
                    unit_mode="reporting",
                )
            finally:
                if renderer is not None:
                    renderer.close()
            return True

        def on_done(_r, canceled, error):
            self._task_finish()
            if error:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"重新识别失败：{tail}（已回滚）")
                messagebox.showerror("重新识别", f"失败：{tail}")
                return
            if canceled:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                self.log("重新识别已取消，未保存任何修改。")
                return
            self._refresh_all()
            still = sum(1 for gi in targets if gi < len(doc.groups) and doc.groups[gi].need_confirm)
            doc.naming_attempted = True
            self.update_status()
            self.log(f"重新识别完成：处理 {len(targets)} 组，仍有 {still} 组待确认"
                     "（人工已确认字段未被覆盖）。")
            messagebox.showinfo(
                "重新识别完成",
                f"已对 {len(targets)} 个待确认分组重新识别；人工已确认的字段保持不变。\n"
                f"仍有 {still} 组需人工确认，请在右侧面板核对。",
            )

        self._start_task("rename_affected", work, on_done, title="重新识别受影响分组")

    def set_separator(self, pages: list[int], value: bool):
        doc = self._active_doc()
        if not doc or self._busy:
            return
        if value and not messagebox.askyesno("排除页面", f"所选原页 {pages} 将从导出文件中排除。\n"
            "如果这里只是新文件的首页，请取消并选择“从此页开始新文件（保留页）”。\n确认这些页无需导出？"):
            return
        self._push_undo()
        for p in pages:
            if 1 <= p <= doc.page_count:
                doc.pages[p - 1].separator = value
                # 记录/撤销"人工确认的分隔页"（v1.9.1）：这是导出时允许排除该页的
                # 保留与排除都是人工决定，重跑分析不得覆盖。
                doc.manual_separator_pages.add(p)  # both keep and exclude are manual decisions
                if value:
                    doc.pending_boundaries.pop(p, None)
                    if p < doc.page_count:
                        doc.manual_boundaries[p+1] = True
                        doc.pending_boundaries.pop(p+1, None)
        grouping.refresh_groups(doc)
        pipeline.update_boundary_state(doc)
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
        doc.manual_boundaries[page_no] = True
        doc.pending_boundaries.pop(page_no, None)
        grouping.refresh_groups(doc)
        pipeline.update_boundary_state(doc)
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
            messagebox.showinfo("识别名称", "尚无拆分草稿。请先点击顶部“开始自动分析”。")
            return
        if any(not g.boundary_confirmed for d in docs for g in d.groups):
            messagebox.showinfo("先检查拆分", "还有文档边界待确认。请先检查待处理问题，并确认分组属于哪一份文件，再进行自动命名。")
            self.review_pending()
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
                        unit_mode="reporting",
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
                self.record_operation_notice("名称提取失败",tail,action="name",doc=docs[0])
                messagebox.showerror("OCR 自动命名", f"命名失败：{tail}")
                return
            if canceled:
                self._restore_ws_snapshot(snap)
                self._refresh_all()
                self.log("OCR 自动命名已取消，未保存任何修改。")
                self.record_operation_notice("名称提取已取消","处理前的名称和人工决定已保留，可以重新识别。",action="name",doc=docs[0],canceled=True)
                return
            # 文档被移除的组不进历史
            for doc in docs:
                if self._doc_still_open(doc):
                    doc.naming_attempted = True
                    for g in doc.groups:
                        if g.unit.strip() and g.unit_confirmed and g.boundary_confirmed:
                            self.unit_history.add(g.unit)
            self._refresh_all()
            n_conf = sum(1 for d in docs if self._doc_still_open(d)
                         for g in d.groups if g.need_confirm)
            if text_layer_only:
                self.log(f"文本层提取完成：共 {total_g} 组，待确认 {n_conf} 组（结果见右侧面板）。")
                return
            self.log(f"OCR 自动命名完成：待确认 {n_conf} 组。")
            if n_conf:
                self.focus_next_name()

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
            # v1.9.1 P0-A：自动检测只写"待确认建议"（rotation_proposal），
            # **绝不**直接写 detected_rotation——后者会立即改变 effective_rotation，
            # 使建议在用户确认前就影响预览 / OCR / 导出。人工已确认的方向也不覆盖。
            for r in results:
                if r.page < 1 or r.page > doc.page_count:
                    continue
                ps = doc.pages[r.page - 1]
                if ps.is_rotation_manual:
                    ps.rotation_proposal = None  # 人工已定，撤销待确认建议
                    continue
                if r.suggested_angle not in (None, 0):
                    ps.rotation_proposal = int(r.suggested_angle) % 360
                    ps.rotation_proposal_confidence = float(r.confidence)
                    ps.rotation_proposal_note = r.suggestion_note or ""
                else:
                    ps.rotation_proposal = None if r.suggested_angle == 0 and r.confidence >= .75 else 0
                    ps.rotation_proposal_confidence = r.confidence
                    ps.rotation_proposal_note = r.suggestion_note
            self._direction_show_results(doc, candidates, sugg, results=results, scope=scope)

        if not self._start_task("direction", work, on_done, title="识别方向（本地 OCR 方向对比）"):
            return

    def _direction_show_results(self, doc, candidates, sugg, results, scope: str = ""):
        ocr_pages: list[int] = [
            r.page for r in results
            if r.suggested_angle not in (None, 0)
        ]
        # OCR 判定内容侧放的页面：**明确询问**是否一键摆正。
        # v1.9.1 P0-A：建议已只写 rotation_proposal（不影响 effective_rotation），
        # 只有用户在此点"是"才写入人工方向；点"否"则方向保持不变（零副作用），
        # 仍可在随后的方向对话框逐页选择。
        if ocr_pages:
            by_page = {r.page: r for r in results}
            desc = "、".join(
                f"第 {p} 页→{by_page[p].suggested_angle}°" for p in ocr_pages
            )
            if messagebox.askyesno(
                "采用方向建议？",
                f"检测到 {len(ocr_pages)} 页内容侧放（横版表格竖着扫描等）：\n"
                f"{desc}\n\n"
                "【是】按建议摆正这些页面（可 Ctrl+Z 撤销）；\n"
                "【否】不采用建议，方向保持不变，可在随后对话框中逐页选择。\n\n"
                "（仅写入页面旋转属性，不重绘内容）",
            ):
                self.apply_rotation(
                    {p: int(by_page[p].suggested_angle) % 360 for p in ocr_pages},
                    doc=doc,
                )
                self.log(f"已采用方向建议并摆正 {len(ocr_pages)} 页：{desc}")
                ocr_pages = []
            else:
                self.log(f"未采用方向建议（{len(ocr_pages)} 页方向保持不变）。")
        pages = sorted(set(sugg) | set(ocr_pages) |
                       {p for p in candidates if doc.pages[p-1].has_rotation_proposal})
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
        """应用人工确认的方向（写入 user_override_rotation）。

        v1.9.1 P0-A：应用即视为人工决定，清除该页的待确认建议；人工方向
        后续不会被自动检测覆盖（见 PageState.is_rotation_manual）。
        """
        doc = doc or self._active_doc()
        if not doc or self._busy:
            return
        self._push_undo()
        for p, angle in mapping.items():
            if 1 <= p <= doc.page_count:
                ps = doc.pages[p - 1]
                ps.rotation = int(angle) % 360
                ps.rotation_proposal = None  # 建议已被采纳，清除暂存
                if doc.analysis_complete:
                    doc.analysis_stale = True
                    doc.pending_boundaries[p] = "方向已变化，请重新自动拆分以更新页面证据"
                    for g in doc.groups:
                        if g.start <= p <= g.end:
                            g.boundary_confirmed = False
                            if not g.title_manual:
                                g.title_confirmed = False
                            if not g.unit_manual:
                                g.unit_confirmed = False
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
                          if s.status == "auto_split" and s.next_page
                          and s.next_page not in doc.manual_boundaries]
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

    def locate_page(self, page_no, doc=None):
        doc = doc or self._active_doc()
        if not doc or not self._doc_still_open(doc) or not 1 <= page_no <= doc.page_count:
            return
        if self._active_doc() is not doc:
            self.doc_index = next(i for i, d in enumerate(self.ws.documents) if d is doc)
            self._refresh_all()
        self.sel_pages = {page_no}
        self.on_page_select(page_no, preview=True)
        self.thumbs.ensure_visible(page_no)

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
        if not doc or self._busy:
            return
        pages = sorted(self.sel_pages) if self.sel_pages else [self.current_page]
        mapping = {}
        for p in pages:
            if 1 <= p <= doc.page_count:
                cur = doc.pages[p - 1].rotation
                mapping[p] = (delta % 360) if absolute else (cur + delta) % 360
        self.apply_rotation(mapping, doc)
        self.log(f"第 {','.join(map(str, pages))} 页旋转 → {doc.pages[pages[0] - 1].rotation}°")

    def rotate_pages_in_group(self, gi: int, delta: int):
        doc = self._active_doc()
        if not doc or self._busy or gi >= len(doc.groups):
            return
        group = doc.groups[gi]
        self.apply_rotation({p: (doc.pages[p - 1].rotation + delta) % 360 for p in group.pages}, doc)
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

    # -- 导出前检查与导出 --------------------------------------------------------

    def export_name_conflicts(self) -> dict[str, int]:
        """统计本工作区所有分组按当前模板生成的**导出文件名**重复次数。

        仅用于导出前提示（导出时 exporter.unique_path 仍会自动加 (2) 兜底）；
        重名意味着用户很可能对多个组填了相同标题/单位，应人工核对。
        """
        template = self.template_var.get() or naming.DEFAULT_TEMPLATE
        counts: dict[str, int] = {}
        for doc in self.ws.documents:
            for gi, g in enumerate(doc.groups):
                name = naming.build_filename(
                    g.title, g.unit, template=template, index=gi + 1,
                    orig=doc.name, start=g.start, end=g.end,
                ) + ".pdf"
                counts[name] = counts.get(name, 0) + 1
        return {k: v for k, v in counts.items() if v > 1}

    def export_summary(self, docs, opts) -> tuple[list[str], list[str], dict]:
        """导出前检查摘要（v1.9.1 阶段五）。

        返回 (field_issues, integrity_issues, stats)：
        - field_issues：字段不完整/待确认/重名等**可放宽**的问题（非严格模式可继续）；
        - integrity_issues：页面覆盖率/异常排除/源文件保真等**不可放宽**的问题
          （fail-closed，即便非严格模式也阻断）；
        - stats：供摘要对话框展示的统计。
        """
        template = opts.template or naming.DEFAULT_TEMPLATE
        needs_title = "{title}" in template
        needs_unit = "{unit}" in template
        field_issues: list[str] = []
        integrity_issues: list[str] = []

        n_groups = sum(len(d.groups) for d in docs)
        n_confirmed = sum(
            1 for d in docs for g in d.groups if not g.need_confirm
        )
        n_pending = n_groups - n_confirmed
        excluded_pages: list[str] = []

        for doc in docs:
            for gi, g in enumerate(doc.groups):
                if needs_title and not g.title.strip():
                    field_issues.append(f"{doc.name} 文档{gi+1}（第{g.start}-{g.end}页）：标题为空")
                if needs_unit and not g.unit.strip():
                    field_issues.append(f"{doc.name} 文档{gi+1}（第{g.start}-{g.end}页）：单位为空白")
                if opts.strict and g.need_confirm:
                    field_issues.append(
                        f"{doc.name} 文档{gi+1}（第{g.start}-{g.end}页）：存在待确认字段"
                    )
                if g.start < 1 or g.end > doc.page_count or g.start > g.end:
                    integrity_issues.append(
                        f"{doc.name} 文档{gi+1}：页码范围无效（{g.start}-{g.end}）"
                    )
            # 异常排除（未确认依据的丢页）→ integrity（fail-closed）
            for msg in exporter.business_integrity_issues(doc):
                integrity_issues.append(msg)
            for p in sorted(doc.separators):
                st = doc.pages[p - 1]
                ctx = getattr(st, "blank_context", "") or "未判定"
                excluded_pages.append(f"{doc.name} 第 {p} 页（依据：{ctx}）")

        # 重名冲突 → field 级（可人工修名后重试，但也不宜静默）
        dup = self.export_name_conflicts()
        for name, cnt in sorted(dup.items()):
            field_issues.append(f"输出文件名重复 ×{cnt}：{name}")

        stats = {
            "n_groups": n_groups,
            "n_confirmed": n_confirmed,
            "n_pending": n_pending,
            "excluded": excluded_pages,
            "n_field": len(field_issues),
            "n_integrity": len(integrity_issues),
            "dup": dup,
        }
        integrity_issues = list(dict.fromkeys(integrity_issues + exporter.integrity_preflight(docs, opts)))
        stats["n_integrity"] = len(integrity_issues)
        return field_issues, integrity_issues, stats

    def _format_export_summary(self, stats: dict) -> str:
        """生成导出前检查摘要文本（简洁、一眼看清）。"""
        lines = [
            "导出前检查摘要：",
            f"· 预计导出 {stats['n_groups']} 份 PDF",
            f"· 名称已确认 {stats['n_confirmed']} 份"
            + (f"，待确认 {stats['n_pending']} 份" if stats["n_pending"] else "（全部已确认）"),
        ]
        n_split = stats.get("n_split")
        if stats.get("excluded"):
            ex = stats["excluded"]
            lines.append(f"· 排除原始页 {len(ex)} 页：" + "、".join(ex[:8])
                         + ("……" if len(ex) > 8 else ""))
        else:
            lines.append("· 无被排除的原始页")
        if stats.get("dup"):
            lines.append("· 重名冲突：" + "、".join(
                f"{n}×{c}" for n, c in list(stats["dup"].items())[:5]))
        else:
            lines.append("· 无重名冲突")
        lines.append("· 漏页/重复页：导出后将做全局覆盖率校验（fail-closed）")
        return "\n".join(lines)

    def export_all(self):
        if self._busy:
            return
        docs = list(self.ws.documents)
        if not docs:
            messagebox.showinfo("导出", "请先打开 PDF 文件。")
            return
        total_g = sum(len(d.groups) for d in docs)
        if not total_g:
            messagebox.showinfo("导出", "尚无拆分草稿。请先点击顶部“开始自动分析”。")
            return
        template = self.template_var.get() or naming.DEFAULT_TEMPLATE
        opts = exporter.ExportOptions(template=template, strict=True)
        field_issues, integrity_issues, stats = self.export_summary(docs, opts)
        self._precheck=(self._check_key(),presentation.precheck_issues(docs,integrity_issues))
        self._refresh_checks()

        # v1.9.1 改进3：统一改用「可勾选清单」对话框，替代一行 askyesno。
        # - 完整性（fail-closed）：只读展示，无「非严格模式」，不可绕过；
        # - 字段级（可放宽）：勾选要导出的组，可选「非严格模式导出」。
        integrity_blocked = bool(integrity_issues)

        def name_of(doc, gi, g):
            return naming.build_filename(
                g.title, g.unit, template=template, index=gi + 1,
                orig=doc.name, start=g.start, end=g.end,
            ) + ".pdf"

        dlg = ExportSummaryDialog(
            self.root, docs, stats, field_issues, integrity_issues,
            name_of=name_of, integrity_blocked=integrity_blocked,
        )
        result = dlg.show()

        if integrity_blocked:
            self.log(f"导出被阻止：{len(integrity_issues)} 项页面完整性问题（fail-closed）。")
            return
        if not result.get("confirmed"):
            self.log("已取消导出。")
            return
        only = set(result["items"])
        if not only:
            self.log("未勾选任何分组，导出取消。")
            return
        opts.strict = bool(result["strict"])
        opts.only = only
        export_key=self._check_key()
        out_dir = filedialog.askdirectory(
            title="清单已确认，请选择保存位置",
            initialdir=self.settings.get("output_dir") or str(Path.home()),
        )
        if not out_dir:
            return
        opts.out_dir = out_dir
        self._last_export = None
        total_g = len(only)
        self._busy = True
        self._update_step_state()

        def work(progress, log, cancel):
            # worker 线程：export_documents 自建 pypdf 读写对象，不触碰 Tk/渲染器
            worker_opts = exporter.ExportOptions(
                template=template, out_dir=out_dir, strict=opts.strict,
                on_log=log, on_progress=progress, cancel_check=cancel,
                only=only,
            )
            return exporter.export_documents(docs, worker_opts)

        def on_done(report, canceled, error):
            self._task_finish()
            if error:
                tail = error.splitlines()[-1] if error else "未知错误"
                self.log(f"导出失败：{tail}")
                self.record_operation_notice("导出任务失败",tail,action="page",doc=docs[0])
                messagebox.showerror("导出", f"导出过程异常：{tail}\n\n已成功导出的文件保留，源文件未受影响。")
                return
            # 报告由 worker 产生，此处仅读（主线程收尾）
            for doc in docs:
                if self._doc_still_open(doc):
                    for g in doc.groups:
                        if g.unit.strip() and g.unit_confirmed and g.boundary_confirmed:
                            self.unit_history.add(g.unit)
            self.settings["output_dir"] = out_dir
            self.settings["template"] = template
            self._save_settings()
            ok, fail = len(report.succeeded), len(report.failed)
            feedback=presentation.export_feedback(docs,only,report,canceled)
            self._last_export=(export_key,feedback)
            self._refresh_checks()
            msg="\n".join(feedback['lines'])
            if feedback['errors']:
                msg += "\n\n需要处理：\n"+"\n".join(feedback['errors'][:8])
                if len(feedback['errors']) > 8:
                    msg += f"\n其余 {len(feedback['errors'])-8} 项请在检查中心查看。"
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
                self.log("正在等待当前处理安全停止，完成后关闭窗口。")
                self.root.after(100, self.on_close)
                return
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
        self.resizable(True, True)
        ttk.Label(self, text="以下页面的方向需要确认。预览后选择相对源 PDF 的额外旋转角度；0°也属于人工决定。",
                  padding=8).pack(anchor="w")
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, width=1000, height=min(480, 60+len(pages)*34), highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        frame = ttk.Frame(canvas, padding=6)
        canvas.create_window((0,0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
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
            ttk.Button(frame, text="预览", width=5, command=lambda pp=p: app.locate_page(pp, doc)).grid(
                row=row, column=7, padx=3)
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
        ttk.Button(btns, text="所列页面保持当前方向", command=lambda: self._keep_current(pages)).pack(side="left", padx=4)
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="left", padx=4)

    def _keep_current(self, pages):
        if messagebox.askyesno("确认方向", "请确认已检查所列页面，保留它们当前的方向。此决定会受到保护，重新识别不会覆盖。", parent=self):
            self._apply_all({p:self.doc.pages[p-1].effective_rotation for p in pages})

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
        ttk.Label(self, text="拆分依据：页码重置 + 首页标题特征双重验证，双信号一致才自动补拆：",
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
        self.title("命名与保存位置")
        self.resizable(False, False)
        self.transient(master)
        self.vars: dict[str, tk.DoubleVar] = {}
        self.val_lbls: dict[str, ttk.Label] = {}

        f = ttk.LabelFrame(self, text="空白检测阈值（修改后需重新运行“检测空白页”）", padding=8)
        # Retain compatibility variables without exposing analysis parameters here.
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
        self.template_var.set(naming.DEFAULT_TEMPLATE)

    def _ok(self):
        for key, var in self.vars.items():
            setattr(self.app.detect_params, key, float(var.get()))
        self.app.template_var.set(self.template_var.get().strip() or naming.DEFAULT_TEMPLATE)
        out = self.out_var.get().strip()
        if out:
            self.app.settings["output_dir"] = out
        self.app._save_settings()
        self.app.log("命名格式与保存位置已保存。")
        self.destroy()


class ExportDoneDialog(tk.Toplevel):
    """导出完成结果：附“打开输出目录”一键入口（Windows 资源管理器）。"""

    def __init__(self, master, out_dir: str, msg: str):
        super().__init__(master)
        self.out_dir = out_dir
        self.title("导出结果")
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
