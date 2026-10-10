"""界面控件：缩略图条（虚拟滚动+徽章+多选+右键菜单）、预览面板、
分组编辑面板、日志面板、进度对话框、Windows 文件拖放支持。

所有渲染均通过 PageRenderer 在 Tk 主线程进行；导出绝不经过本模块。
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from PIL import ImageTk

from . import naming

THUMB_CELL_W = 176
THUMB_CELL_H = 234

BADGE_STYLES = {
    "separator": ("#a93226", "#ffffff"),
    "blank": ("#2e86c1", "#ffffff"),
    "maybe": ("#d68910", "#ffffff"),
    "rotate": ("#5d6d7e", "#ffffff"),
    "split": ("#8e44ad", "#ffffff"),
}


# -- 缩略图条 ---------------------------------------------------------------


class ThumbnailStrip(ttk.Frame):
    """左侧页面缩略图：虚拟滚动、空白/疑似/分隔/旋转徽章、Ctrl/Shift 多选、右键菜单。"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.canvas = tk.Canvas(self, bg="#e8e8e8", highlightthickness=0, width=THUMB_CELL_W)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self._on_scroll)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self._doc = None
        self._renderer = None
        self._photos: dict[tuple, object] = {}
        self._anchor = 1

    # -- 文档与绘制 --------------------------------------------------------

    def set_document(self, doc, renderer):
        self._doc = doc
        self._renderer = renderer
        self._photos.clear()
        self._anchor = 1
        self.canvas.yview_moveto(0.0)
        self.redraw()

    @property
    def _n(self) -> int:
        return len(self._doc.pages) if self._doc else 0

    def redraw(self):
        n = self._n
        total_h = max(n * THUMB_CELL_H, 1)
        self.canvas.configure(scrollregion=(0, 0, THUMB_CELL_W, total_h))
        self.canvas.delete("all")
        vis = set(self._visible_pages())
        for p in self._visible_pages():
            self._draw_page(p)
        for key in list(self._photos):
            if key[0] not in vis:
                del self._photos[key]

    def _visible_pages(self) -> list[int]:
        n = self._n
        if not n:
            return []
        total_h = n * THUMB_CELL_H
        top = self.canvas.yview()[0] * total_h
        h = max(self.canvas.winfo_height(), 200)
        first = max(1, int(top // THUMB_CELL_H) + 1)
        last = min(n, int((top + h) // THUMB_CELL_H) + 2)
        return list(range(first, last + 1))

    def _badges(self, page) -> list[tuple[str, str]]:
        out = []
        if page.separator:
            out.append(("分隔", "separator"))
        if page.split_before:
            out.append(("新文档", "split"))
        if page.blank is not None:
            if page.blank.verdict == "blank":
                out.append(("空白", "blank"))
            elif page.blank.verdict == "maybe":
                out.append(("疑似", "maybe"))
        err = page.effective_rotation % 360
        if err:
            tag = f"{err}°" + ("" if page.is_rotation_manual else "·自动")
            out.append((tag, "rotate"))
        return out

    def _draw_page(self, p: int):
        doc, renderer = self._doc, self._renderer
        if not doc or not renderer:
            return
        page = doc.pages[p - 1]
        y0 = (p - 1) * THUMB_CELL_H
        sel = p in self.app.sel_pages
        if sel:
            self.canvas.create_rectangle(
                0, y0, THUMB_CELL_W, y0 + THUMB_CELL_H,
                fill="#d6e4f0", outline="#4a90d9", width=2,
            )
        img_h = 0
        try:
            rot = page.effective_rotation
            img = renderer.render_thumb(p - 1, user_rotation=rot)
            w, h = img.size
            scale = 150.0 / max(w, 1)
            nw, nh = int(w * scale), int(h * scale)
            key = (p, rot)
            if key not in self._photos:
                pil = img.resize((nw, nh)) if (nw, nh) != img.size else img
                self._photos[key] = ImageTk.PhotoImage(pil)
            self.canvas.create_image(THUMB_CELL_W // 2, y0 + 6, image=self._photos[key], anchor="n")
            img_h = nh
        except Exception:
            pass
        self.canvas.create_text(
            THUMB_CELL_W // 2, y0 + 8 + img_h + 12,
            text=f"第 {p} 页", font=("Segoe UI", 9), fill="#333333",
        )
        bx, by = 4, y0 + 4
        f = tkfont.Font(font=("Segoe UI", 8, "bold"))
        for label, style_key in self._badges(page):
            bg, fg = BADGE_STYLES[style_key]
            w = f.measure(label) + 8
            self.canvas.create_rectangle(bx, by, bx + w, by + 16, fill=bg, outline="")
            self.canvas.create_text(bx + w // 2, by + 8, text=label, fill=fg, font=f)
            by += 19

    # -- 交互 ---------------------------------------------------------------

    def _page_at(self, canvas_y: int) -> int | None:
        if not self._doc:
            return None
        p = int(canvas_y // THUMB_CELL_H) + 1
        return p if 1 <= p <= self._n else None

    def _on_click(self, e):
        p = self._page_at(self.canvas.canvasy(e.y))
        if p is None:
            return
        if e.state & 0x0004:  # Ctrl
            if p in self.app.sel_pages:
                self.app.sel_pages.discard(p)
            else:
                self.app.sel_pages.add(p)
        elif e.state & 0x0001:  # Shift
            a, b = sorted((self._anchor, p))
            self.app.sel_pages = set(range(a, b + 1))
        else:
            self.app.sel_pages = {p}
        self._anchor = p
        self.app.on_page_select(p, preview=True)
        self.redraw()

    def _on_right_click(self, e):
        p = self._page_at(self.canvas.canvasy(e.y))
        if p is None:
            return
        if p not in self.app.sel_pages:
            self.app.sel_pages = {p}
        self._anchor = p
        self.app.on_page_select(p, preview=False)
        page = self._doc.pages[p - 1]
        menu = tk.Menu(self, tearoff=0)
        if page.separator:
            menu.add_command(label="取消分隔标记", command=lambda: self.app.set_separator(list(self.app.sel_pages), False))
        else:
            menu.add_command(label="标记为分隔页（拆分点）", command=lambda: self.app.set_separator(list(self.app.sel_pages), True))
        if page.blank is not None:
            menu.add_command(label="查看空白判定详情", command=lambda: self.app.show_blank_detail(p))
        menu.add_separator()
        menu.add_command(label="旋转 +90°（顺时针）", command=lambda: self.app.rotate_selection(90))
        menu.add_command(label="旋转 -90°（逆时针）", command=lambda: self.app.rotate_selection(-90))
        menu.add_command(label="旋转 180°", command=lambda: self.app.rotate_selection(180))
        menu.add_command(label="恢复 0°", command=lambda: self.app.rotate_selection(0, absolute=True))
        menu.add_separator()
        menu.add_command(label="定位预览", command=lambda: self.app.on_page_select(p, preview=True))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _on_scroll(self, *args):
        """滚动条/滚轮滚动后重绘，保证滚入视口的页面有缩略图。"""
        self.canvas.yview(*args)
        self.redraw()

    def _on_wheel(self, e):
        self.canvas.yview_scroll(-int(e.delta / 120) * 2, "units")
        self.redraw()


# -- 预览面板 ---------------------------------------------------------------


class PreviewPane(ttk.Frame):
    """中央大图预览：适应窗口/实际大小/缩放、Ctrl+滚轮缩放、拖拽平移、
    旋转显示（与导出一致的目标 /Rotate）。

    缩放以 96 DPI 下 100% = 实际打印大小 为基准；渲染按系统 DPI 缩放补偿，
    高分屏上文字同样清晰。
    """

    DPI = 96.0
    MIN_ZOOM = 0.25
    MAX_ZOOM = 4.0

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.zoom = 1.0
        self.fit_mode = True
        self._doc = None
        self._renderer = None
        self._page_no = 1
        self._fit_job = None
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=4, pady=2)
        ttk.Button(bar, text="◀ 上一页", width=9, command=lambda: self.app.step_page(-1)).pack(side="left")
        self.page_lbl = ttk.Label(bar, text="第 - / - 页", width=13, anchor="center")
        self.page_lbl.pack(side="left", padx=4)
        ttk.Button(bar, text="下一页 ▶", width=9, command=lambda: self.app.step_page(1)).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        ttk.Button(bar, text="适应窗口", width=8, command=self._fit).pack(side="left")
        ttk.Button(bar, text="−", width=3, command=lambda: self.zoom_around(1 / 1.25)).pack(side="left")
        self.zoom_lbl = ttk.Label(bar, text="100%", width=9, anchor="center")
        self.zoom_lbl.pack(side="left", padx=2)
        ttk.Button(bar, text="＋", width=3, command=lambda: self.zoom_around(1.25)).pack(side="left")
        ttk.Button(bar, text="实际大小", width=8, command=lambda: self._set_zoom(1.0)).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        ttk.Button(bar, text="逆时针 90°", width=10, command=lambda: self.app.rotate_selection(-90)).pack(side="left")
        ttk.Button(bar, text="顺时针 90°", width=10, command=lambda: self.app.rotate_selection(90)).pack(side="left")
        ttk.Button(bar, text="恢复 0°", width=8, command=lambda: self.app.rotate_selection(0, absolute=True)).pack(side="left")
        self.rot_lbl = ttk.Label(bar, text="", foreground="#5d6d7e")
        self.rot_lbl.pack(side="left", padx=8)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="#5a5a5a", highlightthickness=0)
        hb = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        vb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hb.set, yscrollcommand=vb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vb.grid(row=0, column=1, sticky="ns")
        hb.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self._photo = None
        self._img = None
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))

    # -- 缩放控制 -----------------------------------------------------------

    def _fit(self):
        self.fit_mode = True
        self._render()

    def _set_zoom(self, z: float):
        self.fit_mode = False
        self.zoom = min(max(z, self.MIN_ZOOM), self.MAX_ZOOM)
        self._render()

    def zoom_around(self, factor: float):
        self.fit_mode = False
        self.zoom = min(max(self.zoom * factor, self.MIN_ZOOM), self.MAX_ZOOM)
        self._render()

    def _on_configure(self, _e):
        if self.fit_mode and self._doc is not None:
            if self._fit_job:
                self.after_cancel(self._fit_job)
            self._fit_job = self.after(150, self._render)
        else:
            self._place()

    def _on_wheel(self, e):
        if e.state & 0x0004:  # Ctrl
            self.zoom_around(1.1 if e.delta > 0 else 1 / 1.1)
        else:
            self.canvas.yview_scroll(-int(e.delta / 120) * 2, "units")

    # -- 渲染 ---------------------------------------------------------------

    def clear(self):
        self._photo = None
        self._img = None
        self._doc = None
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 0, 0))
        self.page_lbl.configure(text="第 - / - 页")
        self.rot_lbl.configure(text="")

    def show_guide(self):
        """无文档时的空状态引导：4 步工作流 + 常用快捷键。"""
        self.clear()
        cw = max(self.canvas.winfo_width(), 720)
        ch = max(self.canvas.winfo_height(), 560)
        self.canvas.configure(scrollregion=(0, 0, cw, ch))
        cx = cw // 2
        y = ch // 2 - 120
        self.canvas.create_text(cx, y, text="ScanSplit PDF 使用流程",
                                fill="#ffffff", font=("Segoe UI", 15, "bold"))
        steps = [
            "① 打开 PDF —— 点击“打开 PDF…”或直接把文件拖入窗口（可多选）",
            "② 检测空白页 → 自动分组 —— 空白页自动作为拆分点，可右键微调",
            "③ OCR 自动命名 → 识别方向 —— 本地离线识别，橙色字段请人工确认",
            "④ 导出全部 —— 无损保真导出，可随时撤销（Ctrl+Z）",
        ]
        for s in steps:
            y += 38
            self.canvas.create_text(cx, y, text=s, fill="#e8e8e8", font=("Segoe UI", 11))
        y += 52
        self.canvas.create_text(
            cx, y,
            text="快捷键：Ctrl+O 打开 · Ctrl+E 导出 · Ctrl+Z / Ctrl+Y 撤销/重做 · ←/→ 翻页 · Delete 标记分隔页",
            fill="#b0b0b0", font=("Segoe UI", 9),
        )

    def show(self, doc, renderer, page_no: int, zoom: float | None = None):
        if doc is None:
            self.clear()
            return
        self._doc = doc
        self._renderer = renderer
        self._page_no = page_no
        if zoom is not None:
            self.fit_mode = False
            self.zoom = min(max(zoom, self.MIN_ZOOM), self.MAX_ZOOM)
        self._render()

    def _render(self):
        doc, renderer = self._doc, self._renderer
        if doc is None:
            self.clear()
            return
        page = doc.pages[self._page_no - 1]
        rot = page.effective_rotation % 360
        try:
            w_pt, h_pt = renderer.display_size_pt(self._page_no - 1, user_rotation=rot)
        except Exception as e:
            self.clear()
            self.page_lbl.configure(text=f"第 {self._page_no} / {len(doc.pages)} 页")
            self.app.log(f"预览渲染失败：{e}")
            return
        if self.fit_mode:
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw < 50:
                cw = 700
            if ch < 50:
                ch = 760
            fit = min((cw - 24) / w_pt, (ch - 24) / h_pt) * 72.0 / self.DPI
            self.zoom = min(max(fit, 0.05), self.MAX_ZOOM)
        fx = self.canvas.xview()[0] if self._img is not None else 0.0
        fy = self.canvas.yview()[0] if self._img is not None else 0.0
        try:
            tk_scale = float(self.app.root.tk.call("tk", "scaling")) or 1.0
        except Exception:
            tk_scale = 1.0
        scale = (self.DPI / 72.0) * self.zoom * tk_scale
        scale = min(scale, 2800.0 / max(w_pt, 1.0))  # 单页渲染宽度上限
        try:
            img = renderer.render(
                self._page_no - 1, scale=scale, user_rotation=rot, bucket="preview"
            )
        except Exception as e:
            self.clear()
            self.page_lbl.configure(text=f"第 {self._page_no} / {len(doc.pages)} 页")
            self.app.log(f"预览渲染失败：{e}")
            return
        self._img = img
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self._place()
        self.canvas.xview_moveto(fx)
        self.canvas.yview_moveto(fy)
        self.page_lbl.configure(text=f"第 {self._page_no} / {len(doc.pages)} 页")
        if self.fit_mode:
            self.zoom_lbl.configure(text=f"适应 {int(round(self.zoom * 100))}%")
        else:
            self.zoom_lbl.configure(text=f"{int(round(self.zoom * 100))}%")
        self.rot_lbl.configure(text="未旋转" if rot == 0 else f"旋转 {rot}°（将写入导出）")

    def _place(self):
        if self._photo is None:
            return
        self.canvas.delete("all")
        iw, ih = self._img.width, self._img.height
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        ox = max((cw - iw) // 2, 0)
        oy = max((ch - ih) // 2, 0)
        self.canvas.create_image(ox, oy, image=self._photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, max(iw, cw), max(ih, ch)))


# -- 分组编辑面板 -------------------------------------------------------------


class GroupPanel(ttk.LabelFrame):
    """一个文档组：标题/单位编辑、OCR 候选下拉、置信度与“待确认”徽章、实时文件名。"""

    def __init__(self, master, app, doc, gi: int, group, history_items: list[str]):
        super().__init__(master, padding=6)
        self.app = app
        self.doc = doc
        self.gi = gi
        self.group = group
        self._loading = True

        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text=f"文档 {gi + 1} · 第 {group.start}-{group.end} 页（{group.page_count} 页）",
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        self.confirm_lbl = ttk.Label(head, text="", padding=(5, 0))
        self.confirm_lbl.pack(side="left", padx=6)
        ttk.Button(head, text="预览", width=5, command=lambda: self.app.on_page_select(group.start, preview=True)).pack(side="right")
        ttk.Button(head, text="整组识别方向", width=12,
                   command=lambda: self.app.direction_analyze_group(gi, self.doc)).pack(side="right", padx=2)
        ttk.Button(head, text="整组+90°", width=8, command=lambda: self.app.rotate_pages_in_group(gi, 90)).pack(side="right", padx=2)

        self.title_var = tk.StringVar(value=group.title)
        self.unit_var = tk.StringVar(value=group.unit)

        r1 = ttk.Frame(self)
        r1.pack(fill="x", pady=(6, 2))
        ttk.Label(r1, text="标题", width=5).pack(side="left")
        ttk.Entry(r1, textvariable=self.title_var).pack(side="left", fill="x", expand=True)
        tc = [c.get("text", "") for c in group.title_candidates if c.get("text")]
        self.title_cb = ttk.Combobox(r1, values=tc, width=24, state="readonly")
        if tc:
            self.title_cb.set(tc[0])
        self.title_cb.pack(side="left", padx=(4, 0))
        self.title_cb.bind("<<ComboboxSelected>>", lambda e: self.title_var.set(self.title_cb.get()))
        self.title_conf_lbl = ttk.Label(self, text=self._conf_text(group.title_candidates), foreground="#5d6d7e")
        self.title_conf_lbl.pack(anchor="e")

        r2 = ttk.Frame(self)
        r2.pack(fill="x", pady=2)
        ttk.Label(r2, text="单位", width=5).pack(side="left")
        ttk.Entry(r2, textvariable=self.unit_var).pack(side="left", fill="x", expand=True)
        uc = [c.get("text", "") for c in group.unit_candidates if c.get("text")]
        uc += [h for h in history_items if h not in uc]
        self.unit_cb = ttk.Combobox(r2, values=uc, width=24, state="readonly")
        if uc:
            self.unit_cb.set(uc[0])
        self.unit_cb.pack(side="left", padx=(4, 0))
        self.unit_cb.bind("<<ComboboxSelected>>", lambda e: self.unit_var.set(self.unit_cb.get()))
        self.unit_conf_lbl = ttk.Label(self, text=self._conf_text(group.unit_candidates), foreground="#5d6d7e")
        self.unit_conf_lbl.pack(anchor="e")

        r3 = ttk.Frame(self)
        r3.pack(fill="x", pady=(4, 0))
        ttk.Label(r3, text="导出为").pack(side="left")
        self.fname_lbl = ttk.Label(r3, text="", foreground="#1a6b3c", font=("Segoe UI", 9))
        self.fname_lbl.pack(side="left", padx=4)

        self.title_var.trace_add("write", lambda *a, f="title": self._on_change(f))
        self.unit_var.trace_add("write", lambda *a, f="unit": self._on_change(f))
        self._loading = False
        self._update_filename()
        self._update_confirm_badge()

    def _conf_text(self, cands: list) -> str:
        if not cands:
            return "无候选 · 待确认"
        c = cands[0]
        try:
            conf = float(c.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        s = f"置信 {conf:.0%} · {c.get('source', '')}"
        if conf < 0.55:
            s += " · 待确认"
        return s

    def _on_change(self, field_name: str, *a):
        """字段级确认（v1.8）：编辑哪个字段只确认哪个字段，
        另一个字段的人工待确认状态不受影响。"""
        if self._loading:
            return
        if field_name == "title":
            self.group.title = self.title_var.get().strip()
            self.group.title_confirmed = True
        else:
            self.group.unit = self.unit_var.get().strip()
            self.group.unit_confirmed = True
        self._update_confirm_badge()
        self.app.update_status()
        self._update_filename()

    def _update_confirm_badge(self):
        if not self.group.need_confirm:
            self.confirm_lbl.configure(text="已确认", background="#1e8449", foreground="#ffffff")
        else:
            pending = []
            if not self.group.title_confirmed:
                pending.append("标题")
            if not self.group.unit_confirmed:
                pending.append("单位")
            self.confirm_lbl.configure(
                text=f"{'、'.join(pending)}待确认",
                background="#d68910", foreground="#ffffff",
            )

    def _update_filename(self):
        name = naming.build_filename(
            self.title_var.get(), self.unit_var.get(),
            template=self.app.template_var.get(),
            index=self.gi + 1, orig=self.doc.name,
            start=self.group.start, end=self.group.end,
        )
        self.fname_lbl.configure(text=name + ".pdf")


class GroupPanelArea(ttk.Frame):
    """右侧分组面板滚动区。"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.canvas = tk.Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta / 120) * 2, "units"))
        self.panels = []

    def set_document(self, doc):
        for p in self.panels:
            p.destroy()
        self.panels = []
        if doc is None:
            return
        history = self.app.unit_history.items
        for gi, g in enumerate(doc.groups):
            panel = GroupPanel(self.inner, self.app, doc, gi, g, history)
            panel.pack(fill="x", padx=4, pady=4)
            self.panels.append(panel)
        if not doc.groups:
            ttk.Label(self.inner, text="尚未分组。请先运行“检测空白页”与“自动分组”。",
                      foreground="#5d6d7e").pack(pady=12)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))


# -- 日志与进度 ---------------------------------------------------------------


class LogPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.text = tk.Text(self, height=7, state="disabled", wrap="word", font=("Segoe UI", 9))
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

    def log(self, msg: str):
        self.text.configure(state="normal")
        self.text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.text.see("end")
        self.text.configure(state="disabled")
        try:
            print(f"[ScanSplit] {msg}")
        except Exception:
            pass


class ProgressDialog(tk.Toplevel):
    """模态进度对话框：长操作期间泵 Tk 事件以响应取消按钮。"""

    def __init__(self, master, title="处理中"):
        super().__init__(master)
        self.canceled = False
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.lbl = ttk.Label(self, text="准备中…", width=64, anchor="w")
        self.lbl.pack(padx=12, pady=(12, 6), fill="x")
        self.bar = ttk.Progressbar(self, mode="determinate", length=420)
        self.bar.pack(padx=12, pady=(0, 8), fill="x")
        ttk.Button(self, text="取消", command=self._cancel).pack(pady=(0, 12))
        self.update_idletasks()
        self.grab_set()

    def _cancel(self):
        self.canceled = True
        self.lbl.configure(text="正在取消…")

    def set(self, done: int, total: int, phase: str = ""):
        if total > 0:
            self.bar.configure(maximum=total, value=min(done, total))
        text = f"{phase}  {done}/{total}" if phase else f"{done}/{total}"
        self.lbl.configure(text=text)
        self.update_idletasks()
        self.update()


# -- Windows 拖放 -------------------------------------------------------------


def enable_drag_drop(root, callback) -> None:
    """把文件拖入窗口即导入。callback(list[str]) 在 Tk 主线程调用；失败静默降级。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        root.update_idletasks()
        hwnd = root.winfo_id()
        user32 = ctypes.windll.user32
        WM_DROPFILES = 0x0233
        GWLP_WNDPROC = -4

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        def _proto(func, restype, *argtypes):
            func.restype = restype
            func.argtypes = list(argtypes)

        _proto(user32.DragAcceptFiles, ctypes.c_int, wintypes.HWND, wintypes.BOOL)
        _proto(user32.DragQueryFileW, ctypes.c_uint, wintypes.HANDLE, ctypes.c_uint,
               wintypes.LPWSTR, ctypes.c_uint)
        _proto(user32.DragFinish, None, wintypes.HANDLE)
        _proto(user32.CallWindowProcW, ctypes.c_ssize_t, ctypes.c_ssize_t, wintypes.HWND,
               wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        setter = getattr(user32, "SetWindowLongPtrW", None) or getattr(user32, "SetWindowLongW", None)
        if setter is None:
            return
        _proto(setter, ctypes.c_ssize_t, wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)

        holder = {}

        @WNDPROC
        def _proc(hwnd_, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                hdrop = wparam
                count = user32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                paths = []
                buf = ctypes.create_unicode_buffer(1024)
                for i in range(count):
                    n = user32.DragQueryFileW(hdrop, i, buf, 1024)
                    if n:
                        paths.append(buf.value)
                user32.DragFinish(hdrop)
                if paths:
                    root.after(0, lambda: callback(paths))
                return 0
            return user32.CallWindowProcW(holder["old"], hwnd_, msg, wparam, lparam)

        holder["old"] = setter(hwnd, GWLP_WNDPROC, ctypes.cast(_proc, ctypes.c_void_p).value)
        user32.DragAcceptFiles(hwnd, True)
        root._scan_dnd_proc = _proc  # 保持引用防止被回收
    except Exception:
        pass
