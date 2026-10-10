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

from . import autoname, blankdetect, exporter, grouping, loader, naming, rotation, settings
from .loader import LoadError
from .model import ScanDocument, Workspace
from .ocr import OcrEngine
from .render import PageRenderer, RenderError
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

    def _update_step_state(self, n_docs: int, has_blank_analysis: bool, n_groups: int, n_conf: int):
        """按工作流阶段启用/置灰工具栏按钮，并在状态栏给出“下一步”提示。"""
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

    def undo(self, from_key: bool = False):
        if self._busy or (from_key and self._editing()):
            return
        if not self.undo_stack:
            self.log("没有可撤销的操作。")
            return
        self.redo_stack.append(self.ws.snapshot())
        self.ws.restore(self.undo_stack.pop())
        self._refresh_all()
        self.log("已撤销上一步操作。")

    def redo(self, from_key: bool = False):
        if self._busy or (from_key and self._editing()):
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
        docs = self.ws.documents
        if not docs:
            messagebox.showinfo("检测空白页", "请先打开 PDF 文件。")
            return
        self._push_undo()
        self._busy = True
        total = sum(d.page_count for d in docs)
        counter = {"done": 0}
        prog = ProgressDialog(self.root, "检测空白页")
        try:
            for doc in docs:
                renderer = self.renderers[str(doc.path)]

                def has_text(i: int, reader=doc.reader) -> bool:
                    try:
                        return len((reader.pages[i].extract_text() or "").strip()) > 10
                    except Exception:
                        return False

                def cb(i, n, d=doc):
                    counter["done"] += 1
                    prog.set(counter["done"], total, f"检测 {d.name} 第 {i}/{n} 页")

                results = blankdetect.detect_document(
                    renderer, doc.page_count, self.detect_params, has_text,
                    cb, lambda: prog.canceled,
                )
                for page, res in zip(doc.pages, results):
                    page.blank = res
                doc.status = "analyzed"
                if prog.canceled:
                    break
        finally:
            self._busy = False
            prog.destroy()
        n_blank = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "blank")
        n_maybe = sum(1 for d in docs for p in d.pages if p.blank and p.blank.verdict == "maybe")
        self._refresh_all()
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

    def auto_group_all(self):
        if self._busy:
            return
        docs = self.ws.documents
        if not docs:
            messagebox.showinfo("自动分组", "请先打开 PDF 文件。")
            return
        if not any(p.blank is not None for d in docs for p in d.pages):
            messagebox.showinfo("自动分组", "尚未检测空白页。请先运行“检测空白页”。")
            return
        self._push_undo()
        for doc in docs:
            grouping.auto_group(doc)
            doc.status = "grouped"
            self.log(f"{doc.name}：分隔页 {sorted(doc.separators)}，分组 {len(doc.groups)} 份")
        self._refresh_all()
        messagebox.showinfo(
            "自动分组完成",
            "已按空白候选页拆分。可在缩略图上右键调整分隔页；\n"
            "建议点击“复核拆分”对拆分边界做双重验证。",
        )

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
        docs = self.ws.documents
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
        self._busy = True
        total_g = sum(len(d.groups) for d in docs)
        counter = {"done": 0}
        prog = ProgressDialog(self.root, "OCR 自动命名（本地）")
        try:
            for doc in docs:
                renderer = self.renderers[str(doc.path)]

                def cb(i, n, d=doc):
                    counter["done"] += 1
                    prog.set(counter["done"], total_g, f"命名 {d.name} 第 {i}/{n} 组")

                try:
                    autoname.auto_name_groups(
                        doc, renderer, engine=None if text_layer_only else engine,
                        progress_cb=cb, cancel_cb=lambda: prog.canceled,
                    )
                except Exception as e:
                    self.log(f"命名失败 {doc.name}：{e}")
                for g in doc.groups:
                    if g.unit.strip():
                        self.unit_history.add(g.unit)
                if prog.canceled:
                    break
        finally:
            self._busy = False
            prog.destroy()
        self._refresh_all()
        n_conf = sum(1 for d in docs for g in d.groups if g.need_confirm)
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
        results = []
        ocr_pages: list[int] = []
        if engine.available and candidates:
            # 页面尺寸一致不代表内容正放（横版表格常以侧放方式嵌在纵向页里），
            # 因此对所有候选页做 OCR 方向分析，而不只分析几何不一致的页面。
            self._busy = True
            prog = ProgressDialog(self.root, "识别方向（本地 OCR 方向对比）")
            try:
                results = autoname.analyze_directions(
                    doc, self.renderers[str(doc.path)], candidates,
                    progress_cb=lambda i, n: prog.set(i, n, "识别方向"),
                    cancel_cb=lambda: prog.canceled,
                )
            finally:
                self._busy = False
                prog.destroy()
            ocr_pages = [
                r.page for r in results
                if r.suggested_angle not in (None, 0)
            ]
        elif not engine.available:
            self.log("OCR 不可用，仅按几何尺寸提示方向差异。")
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
        self._busy = True
        prog = ProgressDialog(self.root, "复核拆分 / 智能补拆（本地 OCR）")
        try:
            statuses = autoname.smart_split_review(
                doc, self.renderers[str(doc.path)], engine,
                progress_cb=lambda i, n: prog.set(i, n, "复核拆分"),
                cancel_cb=lambda: prog.canceled,
            )
        finally:
            self._busy = False
            prog.destroy()
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
            self.log(f"智能补拆：第 {','.join(map(str, auto_pages))} 页起为新文档"
                     "（页码重置+标题特征双信号一致，Ctrl+Z 可撤销）")
        BoundaryDialog(self.root, self, doc, statuses)

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
        docs = self.ws.documents
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
        opts = exporter.ExportOptions(template=template, out_dir=out_dir)
        issues = exporter.preflight(docs, opts)
        if issues:
            if not messagebox.askyesno(
                "导出前检查发现问题",
                "以下问题可能导致导出失败或使用占位文件名：\n\n"
                + "\n".join(issues[:15])
                + ("\n……" if len(issues) > 15 else "")
                + "\n\n是否仍然继续导出？",
            ):
                return
        self._busy = True
        prog = ProgressDialog(self.root, "导出")
        opts.on_log = self.log
        opts.on_progress = lambda done, total, phase: prog.set(done, total, phase)
        opts.cancel_check = lambda: prog.canceled
        try:
            report = exporter.export_documents(docs, opts)
        finally:
            self._busy = False
            prog.destroy()
        for doc in docs:
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
        msg = (
            f"导出完成：成功 {ok} 份，失败 {fail} 份。\n"
            f"源文件完整性：{'全部一致（SHA-256 校验通过）' if integrity_ok else '异常！请查看日志'}"
        )
        if report.canceled:
            msg += "\n（已取消，未导出的组不受影响）"
        self.log(f"导出完成：成功 {ok} 份，失败 {fail} 份。")
        if fail:
            self.log(f"导出失败 {fail} 份，详情见上方日志。")
        ExportDoneDialog(self.root, out_dir, msg)

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
