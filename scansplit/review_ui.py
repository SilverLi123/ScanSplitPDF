"""Guided human review with visible evidence and no implicit decisions."""
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
import re
from . import workflow, presentation


def work_area(window):
    """Available desktop on the parent's monitor; never place controls behind the taskbar."""
    try:
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        api.MonitorFromWindow.restype = wintypes.HANDLE
        api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = api.MonitorFromWindow(window.master.winfo_id(), 2)
        if api.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.work
            return r.left, r.top, r.right, r.bottom
    except (AttributeError, OSError, tk.TclError):
        pass
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()-48


def configure_window(window, app):
    left, top, right, bottom = work_area(window)
    aw, ah = max(320, right-left-24), max(240, bottom-top-32)
    w, h = min(1480, aw), min(960, ah)
    x, y = left+(right-left-w)//2, top+(bottom-top-h)//2
    saved = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)",
                         str(app.settings.get("review_window_geometry", "")))
    minimum_w, minimum_h = min(960, aw), min(600, ah)
    if saved:
        sw, sh = map(int,saved.groups()[:2])
        sx, sy = (int(value[1:] if value.startswith("+") else value) for value in saved.groups()[2:])
        w, h = min(aw, max(minimum_w, sw)), min(ah, max(minimum_h, sh))
        x, y = min(max(left, sx), right-w), min(max(top, sy), bottom-h)
    window.resizable(True, True)
    window.minsize(minimum_w, minimum_h)
    # A leading '+' makes a negative monitor coordinate absolute in Tk.
    window.geometry(f"{w}x{h}+{x}+{y}")
    window._normal_geometry = f"{w}x{h}+{x}+{y}"
    if app.settings.get("review_window_maximized", True):
        try:
            window.state("zoomed")
        except tk.TclError:
            pass


class ReviewPage(ttk.Frame):
    def __init__(self, master, app, doc):
        super().__init__(master)
        self.app, self.doc = app, doc
        self.page_no = None
        self.angle = 0
        self.zoom = 1.0
        self.fit_mode = "page"
        self.job = None
        self.photo = None
        bar = ttk.Frame(self, padding=(4, 3))
        bar.pack(fill="x")
        self.caption = ttk.Label(bar, font=("Microsoft YaHei UI", 10, "bold"))
        self.caption.pack(anchor="w")
        self.bind("<Configure>", lambda e: self.caption.configure(wraplength=max(120,e.width-10)),add="+")
        zoom_bar = ttk.Frame(self, padding=(4, 0))
        self.zoom_bar = zoom_bar
        zoom_bar.pack(fill="x")
        for label, factor in [("−", .8), ("+", 1.25), ("适合窗口", 0)]:
            ttk.Button(zoom_bar, text=label, width=7 if not factor else 3,
                       command=lambda f=factor: self.scale(f)).pack(side="right", padx=1)
        ttk.Button(zoom_bar, text="适合宽度", command=self.fit_width).pack(side="right", padx=1)
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
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Double-Button-1>", lambda e: self.fit_width())
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

    def show(self, page_no, caption, angle=None, reset_zoom=True):
        self.page_no = page_no
        self.caption.configure(text=caption)
        self.angle = (self.doc.pages[page_no-1].effective_rotation if angle is None and page_no else angle or 0)
        if reset_zoom:
            self.zoom = 1.0
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.draw()

    def scale(self, factor):
        if not factor:
            self.fit_mode = "page"
        self.zoom = min(4, max(.5, self.zoom*factor)) if factor else 1.0
        self.draw()

    def fit_width(self):
        self.fit_mode, self.zoom = "width", 1.0
        self.draw()

    def wheel(self, event):
        if event.state & 4:
            self.scale(1.25 if event.delta > 0 else .8)
        else:
            axis = self.canvas.xview_scroll if event.state & 1 else self.canvas.yview_scroll
            axis(-int(event.delta / 120)*2, "units")
        return "break"

    def draw(self):
        self.job = None
        self.canvas.delete("all")
        self.photo = None
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        width = max(1, cw-18) if cw > 1 else 280
        height = max(1, ch-18) if ch > 1 else 300
        if not self.page_no:
            self.canvas.create_text(width/2, height/2, text=self.caption.cget("text") or "没有上一页", fill="#49566a")
            return
        if self.app._busy:
            self.canvas.create_text(width/2, height/2, text="正在更新，请稍候…", fill="#49566a")
            return
        try:
            renderer = self.app.renderers[str(self.doc.path)]
            w, h = renderer.display_size_pt(self.page_no-1, self.angle)
            fitted = width if self.fit_mode == "width" else min(width, height*w/h)
            target = min(2400, max(1, int(fitted*self.zoom)))
            fx, fy = self.canvas.xview()[0], self.canvas.yview()[0]
            img = renderer.render(self.page_no-1, width_px=target,
                                  user_rotation=self.angle, bucket="review")
            self.photo = ImageTk.PhotoImage(img, master=self)
            x, y = max(width/2, img.width/2), max(height/2, img.height/2)
            self.canvas.create_image(x, y, image=self.photo)
            self.canvas.configure(scrollregion=(0, 0, max(width, img.width), max(height, img.height)))
            self.canvas.xview_moveto(fx)
            self.canvas.yview_moveto(fy)
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
        self.browser_pages = []
        self.browser_number = None
        self._queue_hidden = False
        self._sash_initialized = False
        self.title(f"检查方向与拆分 — {doc.name}")
        self.transient(master)
        configure_window(self, app)
        self.bind("<Map>", self.first_map, add="+")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(12, 8))
        header.grid(row=0, column=0, sticky="ew")
        tools = ttk.Frame(header)
        tools.pack(side="right", padx=(8, 0))
        self.queue_button = ttk.Button(tools, text="收起列表", command=self.toggle_queue)
        self.queue_button.pack(side="left", padx=3)
        ttk.Button(tools, text="最大化 / 还原", command=self.toggle_maximize).pack(side="left")
        self.heading = ttk.Label(header, font=("Microsoft YaHei UI", 13, "bold"))
        self.heading.pack(anchor="w")
        self.detail = ttk.Label(header, foreground="#49566a", wraplength=1020)
        self.detail.pack(anchor="w", pady=(4, 0))
        self.header_tools = tools
        self.detail.bind("<Configure>", lambda e:self.detail.configure(wraplength=max(200,e.width)))
        body = ttk.PanedWindow(self, orient="horizontal", height=280)
        body.grid(row=1, column=0, sticky="nsew", padx=12)
        self.body = body
        queue = ttk.Frame(body, width=220)
        self.queue = queue
        ttk.Label(queue, text="待检查项目 · 点击可切换", padding=(4, 5)).pack(anchor="w")
        self.tree = ttk.Treeview(queue, columns=("page", "reason"), show="headings", selectmode="browse")
        self.tree.heading("page", text="原页码")
        self.tree.heading("reason", text="检查内容")
        self.tree.column("page", width=65, stretch=False)
        self.tree.column("reason", width=145)
        scroll = ttk.Scrollbar(queue, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.selected(force=False))
        evidence = ttk.Frame(body)
        self.question = ttk.Label(evidence, font=("Microsoft YaHei UI", 11, "bold"), wraplength=800, padding=(6, 4))
        self.question.pack(fill="x")
        self.notebook = ttk.Notebook(evidence)
        self.notebook.pack(fill="both", expand=True)
        self.browser = ttk.Frame(self.notebook)
        previews = ttk.Frame(self.notebook)
        self.comparison = previews
        self.notebook.add(self.browser, text="浏览整份文件")
        self.notebook.add(previews, text="交界两页对照")
        self._create_browser()
        self.left_page = ReviewPage(previews, app, doc)
        self.right_page = ReviewPage(previews, app, doc)
        previews.columnconfigure(0, weight=1, uniform="pages")
        previews.columnconfigure(1, weight=1, uniform="pages")
        previews.rowconfigure(0, weight=1)
        self.left_page.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.right_page.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        body.add(queue, weight=0)
        body.add(evidence, weight=5)
        body.bind("<Configure>", self.layout_queue)
        # A long explanation can scroll, but cannot push decisions off-screen.
        context_host = ttk.Frame(self, height=64)
        context_host.grid(row=2, column=0, sticky="ew", padx=12, pady=4)
        context_host.grid_propagate(False)
        context_host.columnconfigure(0, weight=1)
        context_host.rowconfigure(0, weight=1)
        context_canvas = tk.Canvas(context_host, highlightthickness=0)
        context_canvas.grid(row=0, column=0, sticky="nsew")
        context_scroll = ttk.Scrollbar(context_host, command=context_canvas.yview)
        context_scroll.grid(row=0, column=1, sticky="ns")
        context_canvas.configure(yscrollcommand=context_scroll.set)
        self.context = ttk.Label(context_canvas, padding=(4, 2), wraplength=1000, foreground="#6a4b12")
        context_item = context_canvas.create_window(0, 0, window=self.context, anchor="nw")
        self.context.bind("<Configure>", lambda e: context_canvas.configure(scrollregion=context_canvas.bbox("all")))
        context_canvas.bind("<Configure>", lambda e: (
            context_canvas.itemconfigure(context_item, width=e.width), self.context.configure(wraplength=max(120,e.width-12))))
        self.context.bind("<MouseWheel>", lambda e: context_canvas.yview_scroll(-int(e.delta/120)*2,"units"))
        self.bind("<Configure>",self.window_changed,add="+")
        evidence.bind("<Configure>",lambda e:self.question.configure(wraplength=max(300,e.width-16)))
        self.actions = ttk.Frame(self, padding=(16, 6))
        self.actions.grid(row=3, column=0, sticky="ew")
        self.actions.bind("<Configure>",self.layout_actions)
        ttk.Style(self).configure("ReviewPrimary.TButton",font=("Microsoft YaHei UI",10,"bold"),padding=(8,4))
        self.adjust = ttk.Frame(self, padding=(16, 2))
        self.adjust.grid(row=4, column=0, sticky="ew")
        footer = ttk.Frame(self, padding=(16, 8))
        footer.grid(row=5, column=0, sticky="ew")
        ttk.Label(footer, text="决定可撤销；关闭窗口后，待确认项目仍会保留。", foreground="#49566a").pack(side="left")
        ttk.Button(footer, text="撤销上一步", command=self.undo).pack(side="right", padx=4)
        ttk.Button(footer, text="稍后处理 / 返回主界面", command=self.close).pack(side="right", padx=4)
        self.bind("<Left>", lambda e:self.key_browse(e,-1))
        self.bind("<Right>", lambda e:self.key_browse(e,1))
        self.bind("<Escape>", lambda e:self.close())
        self.refresh()

    def close(self):
        if self._wait_job:
            self.after_cancel(self._wait_job)
            self._wait_job = None
        self.app.settings["review_window_maximized"] = self.state() == "zoomed"
        self.app.settings["review_window_geometry"] = self._normal_geometry
        self.destroy()

    def destroy(self):
        if self._wait_job:
            self.after_cancel(self._wait_job)
            self._wait_job = None
        super().destroy()
        # Release Tcl variables now on the UI thread, rather than later when a
        # closed dialog's Python cycle happens to be collected in an OCR worker.
        for name in ("scope_var","page_var","split_page_var"):
            if hasattr(self,name):
                delattr(self,name)

    def window_changed(self, event):
        if event.widget is self:
            compact = event.height < 800
            if compact:
                self.detail.pack_forget()
            elif not self.detail.winfo_manager():
                self.detail.pack(anchor="w",pady=(4,0))
            self.context.master.master.configure(height=32 if compact else 64)
            if self.state() == "normal":
                self._normal_geometry = self.geometry()

    def layout_actions(self,event):
        row,column,used = 0,0,0
        for button in self.actions.winfo_children():
            width=button.winfo_reqwidth()+8
            if column and used+width > event.width-32:
                row,column,used=row+1,0,0
            button.grid_configure(row=row,column=column)
            used+=width
            column+=1

    def first_map(self, event):
        if event.widget is self and not getattr(self,"_was_mapped",False):
            self._was_mapped = True
            if self.app.settings.get("review_window_maximized",True):
                self.after_idle(lambda:self.state("zoomed") if self.winfo_exists() else None)

    def toggle_maximize(self):
        self.state("normal" if self.state() == "zoomed" else "zoomed")

    def layout_queue(self, event):
        if self._queue_hidden or event.width < 400:
            return
        if not self._sash_initialized:
            self.body.sashpos(0, min(240,max(180,int(event.width*.20))))
            self._sash_initialized = True
        elif self.body.sashpos(0) > event.width-420:
            self.body.sashpos(0,max(160,event.width-420))

    def toggle_queue(self):
        self._queue_hidden = not self._queue_hidden
        if self._queue_hidden:
            self.body.forget(self.queue)
            self.queue_button.configure(text="展开列表")
        else:
            self.body.insert(0,self.queue,weight=0)
            self._sash_initialized = False
            self.queue_button.configure(text="收起列表")

    def _create_browser(self):
        nav = ttk.Frame(self.browser, padding=(4, 4))
        nav.pack(fill="x")
        self.scope_var = tk.StringVar(value="当前文件草稿")
        self.scope = ttk.Combobox(nav,textvariable=self.scope_var,
            values=("当前文件草稿","原 PDF 全部页（含排除页）"),state="readonly",width=26)
        self.scope.pack(side="left")
        self.scope.bind("<<ComboboxSelected>>",lambda e:self.reset_browser())
        self.position = ttk.Label(nav, padding=(8, 0))
        self.position.pack(side="left")
        paging = ttk.Frame(self.browser,padding=(4, 0))
        paging.pack(fill="x")
        self.first_button = ttk.Button(paging,text="首页",width=5,command=lambda:self.browse_at(0))
        self.previous_button = ttk.Button(paging,text="上一页",width=5,command=lambda:self.browse(-1))
        self.next_button = ttk.Button(paging,text="下一页",width=5,command=lambda:self.browse(1))
        self.last_button = ttk.Button(paging,text="末页",width=5,command=lambda:self.browse_at(len(self.browser_pages)-1))
        for button in (self.first_button,self.previous_button,self.next_button,self.last_button):
            button.pack(side="left",padx=(0,4))
        ttk.Label(paging,text="原页码").pack(side="left",padx=(8,2))
        self.page_var = tk.StringVar()
        self.page_entry = ttk.Spinbox(paging,from_=1,to=self.doc.page_count,textvariable=self.page_var,width=5)
        self.page_entry.pack(side="left")
        self.page_entry.bind("<Return>",lambda e:self.jump())
        self.jump_button = ttk.Button(paging,text="跳转",width=5,command=self.jump)
        self.jump_button.pack(side="left",padx=4)
        zoom = ttk.Frame(self.browser,padding=(4,0))
        zoom.pack(fill="x")
        self.browser_page = ReviewPage(self.browser,self.app,self.doc)
        self.browser_page.caption.master.pack_forget()
        self.browser_page.zoom_bar.pack_forget()
        self.browser_page.fit_mode = "page"
        for text,command in (("整页",lambda:self.browser_page.scale(0)),("按宽度",self.browser_page.fit_width),
                             ("+",lambda:self.browser_page.scale(1.25)),("−",lambda:self.browser_page.scale(.8))):
            ttk.Button(zoom,text=text,width=5 if text not in ("+","−") else 2,command=command).pack(side="left",padx=1)
        ttk.Label(zoom,text="滚轮浏览 · Ctrl+滚轮缩放 · 拖动页面平移",foreground="#49566a").pack(side="left",padx=6)
        self.browser_page.pack(fill="both",expand=True)
        self.browse_hint = ttk.Label(self.browser,wraplength=1000,padding=(4,3),foreground="#49566a")
        self.browse_hint.pack(fill="x")
        self.browser.bind("<Configure>",lambda e:self.browse_hint.configure(wraplength=max(200,e.width-12)))

    def current_group(self):
        p = self.page()
        return next((g for g in self.doc.groups if p and g.start <= p <= g.end),None)

    def reset_browser(self, page=None):
        if self.app._busy:
            return
        if self.mode == "direction":
            self.browser_pages = [self.page()] if self.page() else []
            self.scope.configure(state="disabled")
        else:
            self.scope.configure(state="readonly")
            group = self.current_group()
            self.browser_pages = (list(range(1,self.doc.page_count+1)) if self.scope_var.get().startswith("原 PDF")
                else [p for p in (group.pages if group else []) if p not in self.doc.excluded_pages])
        target = page or self.page()
        if target in self.browser_pages:
            index = self.browser_pages.index(target)
        elif self.browser_pages:
            index = 0
        else:
            self.browser_number = None
            self.browser_page.show(None,"当前范围没有保留页面")
            self.position.configure(text="0 页")
            for button in (self.first_button,self.previous_button,self.next_button,self.last_button):
                button.state(["disabled"])
            return
        self.browser_page.zoom = 1.0
        self.browse_at(index)

    def browse_at(self, index):
        if self.app._busy or not 0 <= index < len(self.browser_pages):
            return
        number = self.browser_pages[index]
        self.browser_number = number
        self.page_var.set(str(number))
        self.position.configure(text=f"第 {index+1} / {len(self.browser_pages)} 页 · 原第 {number} 页")
        group = self.current_group()
        caption = (f"原第 {number} 页 · 待确认方向预览" if self.mode=="direction" else
                   f"当前文件草稿 · 原第 {group.start}–{group.end} 页" if group and not self.scope_var.get().startswith("原 PDF") else "原 PDF 全部页面")
        self.browser_page.show(number,caption,self.draft_angle if self.mode=="direction" else None,reset_zoom=False)
        for button,disabled in ((self.first_button,index==0),(self.previous_button,index==0),
                                (self.next_button,index==len(self.browser_pages)-1),(self.last_button,index==len(self.browser_pages)-1)):
            button.state(["disabled"] if disabled else ["!disabled"])
        summary,direction,_ = presentation.page_info(self.doc,number)
        self.browse_hint.configure(text=summary.replace("\n"," · ")+" · "+direction,foreground="#49566a")
        if hasattr(self,"view_split_button") and self.view_split_button.winfo_exists():
            retained = [p for p in group.pages if p not in self.doc.excluded_pages] if group else []
            allowed = bool(self.mode=="groups" and group and number in group.pages
                           and number not in self.doc.excluded_pages and retained and number>retained[0])
            self.view_split_button.state(["!disabled"] if allowed else ["disabled"])

    def browse(self, delta):
        if self.browser_number in self.browser_pages:
            self.browse_at(self.browser_pages.index(self.browser_number)+delta)

    def jump(self):
        try:
            number = int(self.page_var.get())
        except ValueError:
            self.browse_hint.configure(text="请输入有效的原始页码。",foreground="#805200")
            return
        if number not in self.browser_pages:
            message = ("请在左侧列表选择另一张需要确认方向的页面。" if self.mode=="direction" else
                       "该页不在当前浏览范围。可切换到“原 PDF 全部页”查看排除页或其他文件。")
            self.browse_hint.configure(text=message,foreground="#805200")
            return
        self.browse_hint.configure(foreground="#49566a")
        self.browse_at(self.browser_pages.index(number))

    def key_browse(self, event, delta):
        if event.widget.winfo_class() in ("Entry","TEntry","TCombobox","Spinbox","TSpinbox","Treeview"):
            return
        if str(self.notebook.select()) == str(self.browser):
            self.browse(delta)
            return "break"

    def undo(self):
        if not self.app._busy:
            self.app.undo()
            self.refresh()

    def _button(self, text, command, primary=False):
        i=len(self.actions.winfo_children())
        button = ttk.Button(self.actions, text=text, command=command,
                            style="ReviewPrimary.TButton" if primary else "TButton")
        button.grid(row=i//3,column=i%3,sticky="w",padx=(0,8),pady=3)
        return button

    def refresh(self):
        if not self.winfo_exists():
            return
        old = self.tree.selection()
        old_mode = self.mode
        self._selection_key = None
        self.tree.delete(*self.tree.get_children())
        for frame in (self.actions, self.adjust):
            for child in frame.winfo_children():
                child.destroy()
        if self.app._busy:
            self.heading.configure(text="正在按已确认方向更新拆分…")
            self.browser_page.show(None,"正在更新，完成后继续浏览")
            return
        directions = [p for p in self.doc.pages if p.has_rotation_proposal]
        if directions:
            self.mode = "direction"
            self.heading.configure(text=f"先把页面转正 · 剩余 {len(directions)} 页")
            self.detail.configure(text="把大图转到能正常阅读的方向，再点确认。可切换到两页对照检查；旋转按钮只改变预览，确认后才生效。")
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
            self.detail.configure(text="左边是交界前一内容页，右边是交界后页，两页默认完整显示。需要更多上下文时可切换“浏览整份文件”；浏览不会改变确认目标。")
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
            excluded = len(self.doc.excluded_pages)
            self.detail.configure(text=f"点击每份可浏览全部保留页面，再确认范围。源文件 {self.doc.page_count} 页；保留 {self.doc.page_count-excluded} 页；明确排除 {excluded} 页。")
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
            self.view_split_button = ttk.Button(self.adjust,text="以正在查看的页另起一份",command=self.split_viewed)
            self.view_split_button.pack(side="left",padx=8)
        self.notebook.tab(self.browser,text="查看待确认方向" if self.mode=="direction" else "浏览整份文件")
        self.notebook.tab(self.comparison,text="当前与预览方向对照" if self.mode=="direction" else
                          "首页与末页对照" if self.mode=="groups" else "交界两页对照")
        if self.mode == "boundary":
            self.notebook.select(self.comparison)
        elif self.mode != old_mode:
            self.notebook.select(self.browser)
        if self.mode == "update":
            self.browser_page.show(None,"更新分析后可浏览文件")
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

    def selected(self, force=True):
        if self.app._busy:
            return
        p = self.page()
        if not p:
            return
        selection = self.tree.selection()[0]
        key = (self.mode, selection)
        if not force and getattr(self,"_selection_key",None)==key:
            return
        self._selection_key = key
        if self.mode == "direction":
            ps = self.doc.pages[p-1]
            self.draft_angle = ps.effective_rotation
            self.question.configure(text=f"原第 {p} 页：右侧文字是否已经正向？")
            self.left_page.show(p, "当前方向（已生效）")
            self.right_page.show(p, "待确认预览", self.draft_angle)
            self.context.configure(text=f"系统建议：{ps.rotation_proposal_note}。建议尚未应用，请按实际页面判断。")
        elif self.mode == "boundary":
            self.notebook.select(self.comparison)
            self.left_page.fit_mode = self.right_page.fit_mode = "page"
            prev = p-1
            while prev >= 1 and (prev in self.doc.excluded_pages or
                    (self.doc.pages[prev-1].blank and self.doc.pages[prev-1].blank.verdict == "blank")):
                prev -= 1
            self.question.configure(text=f"是否从原第 {p} 页开始一份新文件？")
            self.left_page.show(prev or None, f"交界前一内容页 · 原第 {prev} 页" if prev else "文件开头")
            self.right_page.show(p, f"交界后页 · 原第 {p} 页")
            blank_start = p if self.doc.pages[p-1].blank and self.doc.pages[p-1].blank.verdict=="blank" else p-1
            blank = bool(blank_start >= 1 and self.doc.pages[blank_start-1].blank
                         and self.doc.pages[blank_start-1].blank.verdict=="blank")
            self.blank_button.state(["!disabled"] if blank else ["disabled"])
            self.split_button.state(["!disabled"] if p > 1 else ["disabled"])
            removed = [n for n in range(prev+1,p) if n in self.doc.excluded_pages]
            note = (f"中间原第 {prev+1}–{p-1} 页：{len(removed)} 页从输出排除，其余保留。可切换原 PDF 全部页核对。"
                    if prev < p-1 else "页面默认保留。")
            self.split_button.configure(text=f"确认从原第 {p} 页拆分")
            reason=self.doc.pending_boundaries.get(p) or ("这是人工决定的交界，可对照原页再次核对。" if p in self.doc.manual_boundaries else self.doc.boundary_evidence.get(p,{}).get("reason") or "这是规则自动建立的交界。未保存详细分析依据，请直接对照原页核对。")
            status="规则分析建议 · 需要确认" if p in self.doc.pending_boundaries else "人工决定" if p in self.doc.manual_boundaries else "规则自动结果"
            self.context.configure(text=f"{status}\n判断依据：{reason}\n{note}")
        elif self.mode == "groups":
            i = int(self.tree.selection()[0][2:])
            g = self.doc.groups[i]
            self.question.configure(text=f"文件 {i+1}：原第 {g.start}–{g.end} 页是否属于同一份文件？")
            kept = [n for n in g.pages if n not in self.doc.excluded_pages]
            self.left_page.show(kept[0] if kept else None, f"首张保留页 · 原第 {kept[0]} 页" if kept else "没有保留页")
            self.right_page.show(kept[-1] if kept else None, f"末张保留页 · 原第 {kept[-1]} 页" if kept else "没有保留页")
            self.merge_button.state(["!disabled"] if i else ["disabled"])
            self.split_page_var.set(str(min(g.start+1, g.end)))
            self.context.configure(text="范围正确即可继续；发现误拆可合并，发现漏拆可在下方指定新文件首页。")
        self.reset_browser(p)

    def preview(self, delta):
        p = self.page()
        if p and 1 <= p+delta <= self.doc.page_count:
            self.app.locate_page(p+delta, self.doc)

    def rotate(self, delta):
        if not self.app._busy and self.mode == "direction":
            self.draft_angle = (self.draft_angle+delta) % 360
            self.right_page.show(self.page(), "待确认预览", self.draft_angle)
            self.browser_page.show(self.page(), "待确认方向预览（尚未应用）", self.draft_angle, reset_zoom=False)

    def suggest(self):
        if not self.app._busy and self.mode == "direction":
            self.draft_angle = self.doc.pages[self.page()-1].rotation_proposal or 0
            self.right_page.show(self.page(), "建议方向（尚未确认）", self.draft_angle)
            self.browser_page.show(self.page(), "建议方向预览（尚未确认）", self.draft_angle, reset_zoom=False)

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
        if self.app._busy or self.mode != "groups":
            return
        try:
            p = int(self.split_page_var.get())
        except ValueError:
            self.context.configure(text="请输入有效的原始页码。")
            return
        if not 2 <= p <= self.doc.page_count:
            self.context.configure(text=f"新文件首页应在第 2–{self.doc.page_count} 页之间。")
            return
        group = self.current_group()
        retained = [n for n in group.pages if n not in self.doc.excluded_pages] if group else []
        if not retained or p <= retained[0] or p not in group.pages or p in self.doc.excluded_pages:
            self.context.configure(text="请选择当前文件内部的一张保留页作为新文件首页。不能在其他文件或排除页上拆分。")
            return
        self.app.resolve_boundary(p, True, self.doc)
        self.refresh()

    def split_viewed(self):
        if self.browser_number is not None:
            self.split_page_var.set(str(self.browser_number))
            self.manual_split()

    def finish(self):
        if self.app._busy:
            return
        self.close()
        self.app.workflow_next()
