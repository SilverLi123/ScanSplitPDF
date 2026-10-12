"""Guided human review with visible evidence and no implicit decisions."""
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
from . import workflow


class ReviewPage(ttk.Frame):
    def __init__(self, master, app, doc):
        super().__init__(master)
        self.app, self.doc = app, doc
        self.page_no = None
        self.angle = 0
        self.zoom = 1.0
        self.job = None
        self.photo = None
        bar = ttk.Frame(self, padding=(4, 3))
        bar.pack(fill="x")
        self.caption = ttk.Label(bar, font=("Microsoft YaHei UI", 10, "bold"))
        self.caption.pack(anchor="w")
        self.bind("<Configure>", lambda e: self.caption.configure(wraplength=max(120,e.width-10)),add="+")
        zoom_bar = ttk.Frame(self, padding=(4, 0))
        zoom_bar.pack(fill="x")
        for label, factor in [("−", .8), ("+", 1.25), ("适合窗口", 0)]:
            ttk.Button(zoom_bar, text=label, width=7 if not factor else 3,
                       command=lambda f=factor: self.scale(f)).pack(side="right", padx=1)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, background="#e7ebf0", highlightthickness=0)
        vs = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas.bind("<Configure>", self.schedule)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta / 120)*2, "units"))
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.bind("<Destroy>", self.cleanup)

    def cleanup(self, event):
        if event.widget is self and self.job:
            self.after_cancel(self.job)
            self.job = None

    def schedule(self, event=None):
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(120, self.draw)

    def show(self, page_no, caption, angle=None):
        self.page_no = page_no
        self.caption.configure(text=caption)
        self.angle = (self.doc.pages[page_no-1].effective_rotation if angle is None and page_no else angle or 0)
        self.zoom = 1.0
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.draw()

    def scale(self, factor):
        self.zoom = min(4, max(.5, self.zoom*factor)) if factor else 1.0
        self.draw()

    def draw(self):
        self.job = None
        self.canvas.delete("all")
        self.photo = None
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        width = max(80, cw-18) if cw > 1 else 280
        height = max(80, ch-18) if ch > 1 else 300
        if not self.page_no:
            self.canvas.create_text(width/2, height/2, text="没有上一页", fill="#49566a")
            return
        if self.app._busy:
            self.canvas.create_text(width/2, height/2, text="正在更新，请稍候…", fill="#49566a")
            return
        try:
            renderer = self.app.renderers[str(self.doc.path)]
            w, h = renderer.display_size_pt(self.page_no-1, self.angle)
            target = min(2400, max(150, int(min(width, height*w/h)*self.zoom)))
            img = renderer.render(self.page_no-1, width_px=target,
                                  user_rotation=self.angle, bucket="review")
            self.photo = ImageTk.PhotoImage(img, master=self)
            x, y = max(width/2, img.width/2), max(height/2, img.height/2)
            self.canvas.create_image(x, y, image=self.photo)
            self.canvas.configure(scrollregion=(0, 0, max(width, img.width), max(height, img.height)))
        except Exception as exc:
            self.canvas.create_text(width/2, height/2, width=width-20,
                                    text=f"预览失败，请稍后重试：{exc}", fill="#a23b25")
            self.app.log(f"复核预览失败：{exc}")


class ReviewDialog(tk.Toplevel):
    def __init__(self, master, app, doc):
        super().__init__(master)
        self.app, self.doc = app, doc
        self.mode = None
        self.draft_angle = 0
        self._waiting = False
        self._wait_job = None
        self._all_boundaries = False
        self.title(f"检查方向与拆分 — {doc.name}")
        self.geometry(f"{min(1180,self.winfo_screenwidth()-80)}x{min(820,self.winfo_screenheight()-100)}")
        self.minsize(960, 640)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)
        header = ttk.Frame(self, padding=(16, 12))
        header.pack(fill="x")
        self.heading = ttk.Label(header, font=("Microsoft YaHei UI", 13, "bold"))
        self.heading.pack(anchor="w")
        self.detail = ttk.Label(header, foreground="#49566a", wraplength=1020)
        self.detail.pack(anchor="w", pady=(4, 0))
        body = ttk.PanedWindow(self, orient="horizontal", height=280)
        body.pack(fill="both", expand=True, padx=12)
        queue = ttk.Frame(body, width=210)
        ttk.Label(queue, text="待检查项目 · 点击可切换", padding=(4, 5)).pack(anchor="w")
        self.tree = ttk.Treeview(queue, columns=("page", "reason"), show="headings", selectmode="browse")
        self.tree.heading("page", text="原页码")
        self.tree.heading("reason", text="检查内容")
        self.tree.column("page", width=65, stretch=False)
        self.tree.column("reason", width=140)
        scroll = ttk.Scrollbar(queue, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.selected())
        evidence = ttk.Frame(body)
        self.question = ttk.Label(evidence, font=("Microsoft YaHei UI", 11, "bold"), wraplength=800, padding=(6, 4))
        self.question.pack(fill="x")
        previews = ttk.Frame(evidence)
        previews.pack(fill="both", expand=True)
        self.left_page = ReviewPage(previews, app, doc)
        self.right_page = ReviewPage(previews, app, doc)
        previews.columnconfigure(0, weight=1, uniform="pages")
        previews.columnconfigure(1, weight=1, uniform="pages")
        previews.rowconfigure(0, weight=1)
        self.left_page.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.right_page.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        body.add(queue, weight=1)
        body.add(evidence, weight=5)
        body.bind("<Configure>", lambda e: body.sashpos(0, min(320, max(260, int(e.width*.27)))))
        self.context = ttk.Label(self, padding=(16, 5), wraplength=1000, foreground="#6a4b12")
        self.context.pack(fill="x")
        self.bind("<Configure>",lambda e:self.context.configure(wraplength=max(400,self.winfo_width()-40)),add="+")
        evidence.bind("<Configure>",lambda e:self.question.configure(wraplength=max(300,e.width-16)))
        self.actions = ttk.Frame(self, padding=(16, 6))
        self.actions.pack(fill="x")
        self.adjust = ttk.Frame(self, padding=(16, 2))
        self.adjust.pack(fill="x")
        footer = ttk.Frame(self, padding=(16, 8))
        footer.pack(fill="x")
        ttk.Label(footer, text="决定可撤销；关闭窗口后，待确认项目仍会保留。", foreground="#49566a").pack(side="left")
        ttk.Button(footer, text="撤销上一步", command=self.undo).pack(side="right", padx=4)
        ttk.Button(footer, text="稍后处理 / 返回主界面", command=self.close).pack(side="right", padx=4)
        self.refresh()

    def close(self):
        if self._wait_job:
            self.after_cancel(self._wait_job)
        self.destroy()

    def undo(self):
        if not self.app._busy:
            self.app.undo()
            self.refresh()

    def _button(self, text, command, primary=False):
        i=len(self.actions.winfo_children())
        button = ttk.Button(self.actions, text=text, command=command,
                            style="Primary.TButton" if primary else "TButton")
        button.grid(row=i//3,column=i%3,sticky="w",padx=(0,8),pady=3)
        return button

    def refresh(self):
        if not self.winfo_exists():
            return
        old = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for frame in (self.actions, self.adjust):
            for child in frame.winfo_children():
                child.destroy()
        if self.app._busy:
            self.heading.configure(text="正在按已确认方向更新拆分…")
            return
        directions = [p for p in self.doc.pages if p.has_rotation_proposal]
        if directions:
            self.mode = "direction"
            self.heading.configure(text=f"先把页面转正 · 剩余 {len(directions)} 页")
            self.detail.configure(text="把右侧预览转到能正常阅读的方向，再点确认。旋转按钮仅改变此处预览，确认后才生效。")
            for p in directions:
                self.tree.insert("", "end", iid=f"d:{p.src_index}", values=(p.src_index, "方向待确认"))
            self._button("向左转 90°", lambda: self.rotate(-90))
            self._button("向右转 90°", lambda: self.rotate(90))
            self._button("预览建议方向", self.suggest)
            self._button("方向正确，确认下一页", self.confirm_direction, True)
        elif self.doc.analysis_stale:
            self.mode = "update"
            self.heading.configure(text="方向已确认，需要更新拆分")
            self.detail.configure(text="按你确认的方向重新分析，不覆盖人工拆分和名称。")
            self._button("更新拆分并继续检查", self.reanalyze, True)
            self.left_page.show(None, "")
            self.right_page.show(None, "")
        elif self.doc.pending_boundaries or self._all_boundaries:
            self.mode = "boundary"
            self.heading.configure(text=f"判断文件交界 · 剩余 {len(self.doc.pending_boundaries)} 处")
            self.detail.configure(text="比较前后两页：右侧是另一份文件的首页，还是左侧文件的续页 / 附件？")
            pages=set(self.doc.pending_boundaries)
            if self._all_boundaries:
                pages.update(g.start for g in self.doc.groups[1:])
                self.heading.configure(text="审核文件交界 · 自动结果与人工决定分开显示")
            for p in sorted(pages):
                label="需要确认" if p in self.doc.pending_boundaries else "人工确认交界" if p in self.doc.manual_boundaries else "规则建立交界"
                self.tree.insert("", "end", iid=str(p), values=(p,label))
            self.split_button = self._button("确认从这里拆分（保留页面）", lambda: self.resolve(True), True)
            self._button("属于上一份（保留页面）", lambda: self.resolve(False))
            self._button("稍后处理此处",self.skip_current)
            self.blank_button = self._button("排除已确认的分隔空白…", self.exclude_blank)
            if self._all_boundaries:
                self._button("返回文件范围总览",self.overview)
        else:
            self.mode = "groups"
            self.heading.configure(text=f"检查拆分总览 · 当前 {len(self.doc.groups)} 份文件")
            excluded = len(self.doc.separators)
            self.detail.configure(text=f"点击每份查看首页和末页，确认页码范围。源文件 {self.doc.page_count} 页；保留 {self.doc.page_count-excluded} 页；明确排除 {excluded} 页。")
            for i, g in enumerate(self.doc.groups):
                status="人工确认" if all(p in self.doc.manual_boundaries for p in g.pages) else "规则通过" if g.boundary_confirmed else "待确认"
                self.tree.insert("", "end", iid=f"g:{i}", values=(f"{g.start}–{g.end}", f"文件 {i+1} · {status}"))
            self._button("这份的页码范围正确", self.confirm_group, True)
            self.merge_button = self._button("与上一份合并", self.merge_previous)
            self._button("检查完成，继续下一步", self.finish)
            self._button("审核全部文件交界",self.all_boundaries)
            ttk.Label(self.adjust, text="发现漏拆？输入第二份文件的原始首页：").pack(side="left")
            self.split_page_var = tk.StringVar()
            ttk.Spinbox(self.adjust, from_=2, to=self.doc.page_count, width=6, textvariable=self.split_page_var).pack(side="left", padx=4)
            ttk.Button(self.adjust, text="从这页另起一份（不删页）", command=self.manual_split).pack(side="left")
        children = self.tree.get_children()
        if children:
            chosen = old[0] if old and old[0] in children else children[0]
            self.tree.selection_set(chosen)
            self.tree.see(chosen)
            self.selected()

    def page(self):
        selection = self.tree.selection()
        if not selection:
            return None
        value = selection[0]
        if value.isdigit():
            return int(value)
        if value.startswith("d:"):
            return int(value[2:])
        if value.startswith("g:"):
            return self.doc.groups[int(value[2:])].start
        return None

    def selected(self):
        if self.app._busy:
            return
        p = self.page()
        if not p:
            return
        if self.mode == "direction":
            ps = self.doc.pages[p-1]
            self.draft_angle = ps.effective_rotation
            self.question.configure(text=f"原第 {p} 页：右侧文字是否已经正向？")
            self.left_page.show(p, "当前方向（已生效）")
            self.right_page.show(p, "待确认预览", self.draft_angle)
            self.context.configure(text=f"系统建议：{ps.rotation_proposal_note}。建议尚未应用，请按实际页面判断。")
        elif self.mode == "boundary":
            prev = p-1
            while prev >= 1 and self.doc.pages[prev-1].blank and self.doc.pages[prev-1].blank.verdict == "blank":
                prev -= 1
            self.question.configure(text=f"是否从原第 {p} 页开始一份新文件？")
            self.left_page.show(prev or None, f"前一内容页 · 原第 {prev} 页" if prev else "文件开头")
            self.right_page.show(p, f"待判断页 · 原第 {p} 页")
            blank = bool(prev < p-1 or (self.doc.pages[p-1].blank and self.doc.pages[p-1].blank.verdict == "blank"))
            self.blank_button.state(["!disabled"] if blank else ["disabled"])
            self.split_button.state(["!disabled"] if p > 1 else ["disabled"])
            note = f"中间第 {prev+1}–{p-1} 页为空白候选，当前仍保留。" if prev < p-1 else "页面默认保留。"
            reason=self.doc.pending_boundaries.get(p) or ("这是人工决定的交界，可对照原页再次核对。" if p in self.doc.manual_boundaries else self.doc.boundary_evidence.get(p,{}).get("reason") or "这是规则自动建立的交界。未保存详细分析依据，请直接对照原页核对。")
            status="规则分析建议 · 需要确认" if p in self.doc.pending_boundaries else "人工决定" if p in self.doc.manual_boundaries else "规则自动结果"
            self.context.configure(text=f"{status}\n判断依据：{reason}\n{note}")
        elif self.mode == "groups":
            i = int(self.tree.selection()[0][2:])
            g = self.doc.groups[i]
            self.question.configure(text=f"文件 {i+1}：原第 {g.start}–{g.end} 页是否属于同一份文件？")
            self.left_page.show(g.start, f"首页 · 原第 {g.start} 页")
            self.right_page.show(g.end, f"末页 · 原第 {g.end} 页")
            self.merge_button.state(["!disabled"] if i else ["disabled"])
            self.split_page_var.set(str(min(g.start+1, g.end)))
            self.context.configure(text="范围正确即可继续；发现误拆可合并，发现漏拆可在下方指定新文件首页。")

    def preview(self, delta):
        p = self.page()
        if p and 1 <= p+delta <= self.doc.page_count:
            self.app.locate_page(p+delta, self.doc)

    def rotate(self, delta):
        if not self.app._busy and self.mode == "direction":
            self.draft_angle = (self.draft_angle+delta) % 360
            self.right_page.show(self.page(), "待确认预览", self.draft_angle)

    def suggest(self):
        if not self.app._busy and self.mode == "direction":
            self.draft_angle = self.doc.pages[self.page()-1].rotation_proposal or 0
            self.right_page.show(self.page(), "建议方向（尚未确认）", self.draft_angle)

    def confirm_direction(self):
        if self.app._busy or self.mode != "direction":
            return
        self.app.apply_rotation({self.page():self.draft_angle}, self.doc)
        if not any(p.has_rotation_proposal for p in self.doc.pages):
            self.reanalyze()
        else:
            self.refresh()

    def reanalyze(self):
        if self.app._busy or self._waiting:
            return
        self._waiting = True
        self.app.automatic_split(only_doc=self.doc, on_complete=self.updated)
        self.refresh()
        self._wait_job = self.after(150, self.wait_updated)

    def wait_updated(self):
        self._wait_job = None
        if self.app._busy:
            self._wait_job = self.after(150, self.wait_updated)
        else:
            self._waiting = False
            self.refresh()

    def updated(self):
        self._waiting = False
        self.refresh()

    def resolve(self, split):
        p = self.page()
        if p and self.mode == "boundary" and not self.app._busy:
            was_pending = bool(self.doc.pending_boundaries)
            children=list(self.tree.get_children())
            next_i=children[(children.index(str(p))+1)%len(children)] if children else None
            self.app.resolve_boundary(p, split, self.doc)
            self.refresh()
            if was_pending and not self.doc.pending_boundaries:
                self.reanalyze()  # One continuation after the last unresolved boundary.
                return
            if self._all_boundaries and next_i and self.tree.exists(next_i):
                self.tree.selection_set(next_i)
                self.selected()

    def skip_current(self):
        if self.app._busy:
            return
        children=list(self.tree.get_children())
        selected=self.tree.selection()
        if children and selected:
            i=(children.index(selected[0])+1)%len(children)
            self.tree.selection_set(children[i])
            self.tree.see(children[i])
            self.selected()

    def all_boundaries(self):
        self._all_boundaries=True
        self.refresh()

    def overview(self):
        self._all_boundaries=False
        self.refresh()

    def exclude_blank(self):
        p = self.page()
        if p and self.mode == "boundary" and not self.app._busy:
            self.app.exclude_confirmed_blank(p, self.doc)
            self.refresh()

    def confirm_group(self):
        if self.app._busy or not self.tree.selection():
            return
        i = int(self.tree.selection()[0][2:])
        self.app.confirm_group_boundary(self.doc, i)
        self.refresh()
        if i+1 < len(self.doc.groups):
            self.tree.selection_set(f"g:{i+1}")
            self.selected()

    def merge_previous(self):
        if self.app._busy or self.mode != "groups":
            return
        p = self.page()
        if p and p > 1:
            self.app.resolve_boundary(p, False, self.doc)
            self.refresh()

    def manual_split(self):
        if self.app._busy:
            return
        try:
            p = int(self.split_page_var.get())
        except ValueError:
            self.context.configure(text="请输入有效的原始页码。")
            return
        if not 2 <= p <= self.doc.page_count:
            self.context.configure(text=f"新文件首页应在第 2–{self.doc.page_count} 页之间。")
            return
        self.app.resolve_boundary(p, True, self.doc)
        self.refresh()

    def finish(self):
        if self.app._busy:
            return
        self.close()
        self.app.workflow_next()
