"""界面控件：缩略图条（虚拟滚动+徽章+多选+右键菜单）、预览面板、
分组编辑面板、日志面板、进度对话框、Windows 文件拖放支持。

所有渲染均通过 PageRenderer 在 Tk 主线程进行；导出绝不经过本模块。
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

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
    "internal": ("#1e8449", "#ffffff"),
    "uncertain": ("#d68910", "#ffffff"),
    "proposal": ("#8e44ad", "#ffffff"),
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
                # 业务属性区分（v1.9.1 改进1）：分隔 vs 内部空白 vs 待确认
                ctx = getattr(page, "blank_context", "") or ""
                if ctx == "internal_blank":
                    out.append(("内部空白", "internal"))
                elif ctx == "uncertain":
                    out.append(("待确认", "uncertain"))
                else:
                    out.append(("空白", "blank"))
            elif page.blank.verdict == "maybe":
                out.append(("疑似", "maybe"))
        err = page.effective_rotation % 360
        if err:
            tag = f"{err}°" + ("" if page.is_rotation_manual else "·自动")
            out.append((tag, "rotate"))
        if getattr(page, "has_rotation_proposal", False):
            out.append((f"建议{int(page.rotation_proposal) % 360}°", "proposal"))
        return out

    def _page_strip_color(self, page) -> str | None:
        """页面左侧色带颜色（v1.9.1 改进1）：按业务属性直观区分。"""
        if page.separator:
            return "#a93226"                     # 分隔页（导出排除）
        ctx = getattr(page, "blank_context", "") or ""
        if page.blank is not None and page.blank.verdict == "blank":
            if ctx == "internal_blank":
                return "#1e8449"                 # 内部空白（保留）
            if ctx == "uncertain":
                return "#d68910"                 # 待确认（默认保留）
        if getattr(page, "has_rotation_proposal", False):
            return "#8e44ad"                     # 有方向建议待处理
        if page.split_before:
            return "#8e44ad"                     # 新文档起点
        return None

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
        # 左侧业务属性色带（分隔/内部空白/待确认/方向建议/新文档）
        strip = self._page_strip_color(page)
        if strip:
            self.canvas.create_rectangle(0, y0, 5, y0 + THUMB_CELL_H, fill=strip, outline="")
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
            menu.add_command(label="从此页开始新文件（保留页）", command=lambda: self.app.add_split_before(p))
            menu.add_command(label="排除选中页（分隔空白）", command=lambda: self.app.set_separator(list(self.app.sel_pages), True))
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

    def ensure_visible(self, page_no: int):
        """把指定页滚动到缩略图视口内（分组面板定位时用）。"""
        if not self._doc or not (1 <= page_no <= self._n):
            return
        total_h = max(self._n * THUMB_CELL_H, 1)
        target = (page_no - 1) * THUMB_CELL_H
        view_h = max(self.canvas.winfo_height(), 200)
        top = self.canvas.yview()[0] * total_h
        if target < top or target + THUMB_CELL_H > top + view_h:
            self.canvas.yview_moveto(max(0.0, min(1.0, target / total_h)))
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
        ttk.Button(bar, text="上一页", width=6, command=lambda: self.app.step_page(-1)).pack(side="left")
        self.page_lbl = ttk.Label(bar, text="第 - / - 页", width=11, anchor="center")
        self.page_lbl.pack(side="left", padx=4)
        ttk.Button(bar, text="下一页", width=6, command=lambda: self.app.step_page(1)).pack(side="left")
        zoom_bar = ttk.Frame(self)
        zoom_bar.pack(fill="x", padx=4, pady=2)
        ttk.Button(zoom_bar, text="适应窗口", width=8, command=self._fit).pack(side="left")
        ttk.Button(zoom_bar, text="−", width=3, command=lambda: self.zoom_around(1 / 1.25)).pack(side="left")
        self.zoom_lbl = ttk.Label(zoom_bar, text="100%", width=5, anchor="center")
        self.zoom_lbl.pack(side="left", padx=2)
        ttk.Button(zoom_bar, text="＋", width=3, command=lambda: self.zoom_around(1.25)).pack(side="left")
        rotate_menu = ttk.Menubutton(zoom_bar, text="旋转")
        menu = tk.Menu(rotate_menu, tearoff=False)
        for text, command in [("向左转 90°", lambda: self.app.rotate_selection(-90)),
                              ("向右转 90°", lambda: self.app.rotate_selection(90)),
                              ("恢复原方向", lambda: self.app.rotate_selection(0, absolute=True))]:
            menu.add_command(label=text, command=command)
        rotate_menu.configure(menu=menu)
        rotate_menu.pack(side="left", padx=4)
        self.rot_lbl = ttk.Label(self, text="", foreground="#49566a", wraplength=480)
        self.rot_lbl.pack(anchor="w", padx=8)
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
            "选择 PDF，然后按顶部的大按钮继续",
            "检查时直接比较两页：是新文件，还是上一份的续页？",
            "拆分确认后，核对标题与填报单位，再导出",
            "拿不准可以稍后处理，待确认结果不会被导出",
        ]
        for s in steps:
            y += 38
            self.canvas.create_text(cx, y, text=s, fill="#e8e8e8", font=("Segoe UI", 11))
        y += 52
        self.canvas.create_text(
            cx, y,
            text="全程本地离线 · 源 PDF 保留 · 不确定的空白页默认保留",
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
            self.zoom_lbl.configure(text=f"{int(round(self.zoom * 100))}%")
        else:
            self.zoom_lbl.configure(text=f"{int(round(self.zoom * 100))}%")
        # v1.9.1 改进2：预览内明确区分"已生效方向"与"待确认建议"，
        # 建议绝不改动画布朝向，仅在文字/描边层面提示（避免误以为已生效）。
        prop = int(page.rotation_proposal) % 360 if getattr(page, "has_rotation_proposal", False) else None
        if prop is not None:
            self.rot_lbl.configure(
                text=f"{('未旋转' if rot == 0 else f'当前 {rot}°')} ｜ 建议 {prop}°（待确认）",
                foreground="#8e44ad",
            )
        else:
            self.rot_lbl.configure(
                text="未旋转" if rot == 0 else f"旋转 {rot}°（将写入导出）",
                foreground="#5d6d7e",
            )
        self._draw_highlight(page, prop)

    def _draw_highlight(self, page, proposal_deg):
        """在预览页四周画描边：方向建议=紫色虚线（未生效），
        已生效旋转=蓝色实线，分隔页=红色实线（导出排除）。"""
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if page.separator:
            self.canvas.create_rectangle(
                2, 2, cw - 3, ch - 3, outline="#a93226", width=3, dash=(8, 4), tags="hl",
            )
            self.canvas.create_text(
                cw // 2, 16, text="分隔页 · 导出时排除", fill="#a93226",
                font=("Segoe UI", 10, "bold"), tags="hl",
            )
        elif proposal_deg is not None:
            self.canvas.create_rectangle(
                2, 2, cw - 3, ch - 3, outline="#8e44ad", width=3, dash=(8, 4), tags="hl",
            )
            self.canvas.create_text(
                cw // 2, 16,
                text=f"方向建议 {proposal_deg}° 待确认 · 请在上方“识别方向”中采用或忽略",
                fill="#8e44ad", font=("Segoe UI", 10, "bold"), tags="hl",
            )

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
        # 缩放/平移重绘后保持高亮描边在最上层
        doc = self._doc
        if doc is not None:
            page = doc.pages[self._page_no - 1]
            prop = int(page.rotation_proposal) % 360 if getattr(page, "has_rotation_proposal", False) else None
            rot = page.effective_rotation % 360
            self._draw_highlight(page, prop)

    def show_evidence(self, bbox, image_size):
        """Show the OCR evidence on the current oriented preview, without editing PDF."""
        if self._img is None:
            return
        iw, ih = self._img.size
        w, h = image_size
        if w <= 0 or h <= 0:
            return
        ox = max((self.canvas.winfo_width() - iw) // 2, 0)
        oy = max((self.canvas.winfo_height() - ih) // 2, 0)
        x0, y0, x1, y1 = bbox
        self.canvas.delete("evidence")
        self.canvas.create_rectangle(ox+x0*iw/w, oy+y0*ih/h, ox+x1*iw/w, oy+y1*ih/h,
                                     outline="#ef8c00", width=3, tags="evidence")


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
        self.header_label = ttk.Label(head, text=f"文档 {gi + 1} · 第 {group.start}-{group.end} 页（{group.page_count} 页）",
                  font=("Segoe UI", 10, "bold"),wraplength=450)
        self.header_label.pack(anchor="w")
        self.confirm_lbl = ttk.Label(head, text="", padding=(5, 0),wraplength=450)
        self.confirm_lbl.pack(anchor="w", pady=(4, 0))
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=2)
        ttk.Button(actions, text="预览首页", width=8, command=lambda: self._preview(group.start)).pack(side="left")
        ttk.Button(actions, text="查看 / 调整拆分", command=self.app.review_pending).pack(side="left", padx=4)

        # 边界前后页查看：判断是否误拆（上一边界前页 / 本组首页 / 本组末页 / 下一边界后页）
        bd = ttk.Frame(self)
        bd.pack(fill="x", pady=(2, 0))
        ttk.Label(bd, text="页码：", foreground="#49566a").pack(side="left")
        prev_p = group.start - 1
        next_p = group.end + 1
        ttk.Button(bd, text="前页", width=5,
                   command=lambda p=prev_p: self._preview(p)).pack(side="left", padx=1)
        ttk.Button(bd, text="首页", width=5,
                   command=lambda: self._preview(group.start)).pack(side="left", padx=1)
        ttk.Button(bd, text="末页", width=5,
                   command=lambda: self._preview(group.end)).pack(side="left", padx=1)
        ttk.Button(bd, text="后页", width=5,
                   command=lambda p=next_p: self._preview(p)).pack(side="left", padx=1)

        self.title_var = tk.StringVar(value=group.title)
        self.unit_var = tk.StringVar(value=group.unit)

        name_host = ttk.Frame(self)
        if group.boundary_confirmed and not doc.analysis_stale:
            name_host.pack(fill="x")
        else:
            ttk.Label(self, text="先检查这一份的方向和拆分，再识别名称。",
                      foreground="#6a4b12", wraplength=360).pack(anchor="w", pady=8)

        r1 = ttk.Frame(name_host)
        r1.pack(fill="x", pady=(6, 2))
        r1.columnconfigure(1, weight=1)
        ttk.Label(r1, text="标题", width=8).grid(row=0,column=0,sticky="w")
        self.title_entry = ttk.Entry(r1, textvariable=self.title_var, width=16)
        self.title_entry.grid(row=0,column=1,sticky="ew")
        title_options = ttk.Frame(name_host)
        title_options.pack(fill="x", pady=2)
        ttk.Label(title_options, text="候选", width=5).pack(side="left")
        # 候选下拉：按置信度降序展示，标注置信度与来源（v1.9.1 改进5）
        self.title_cb_map = self._build_candidate_map(group.title_candidates)
        self.title_cb = ttk.Combobox(title_options, values=list(self.title_cb_map.values()), width=16, state="readonly")
        if self.title_cb_map:
            self.title_cb.set(next(iter(self.title_cb_map.values())))
        self.title_cb.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.title_cb.bind("<<ComboboxSelected>>", lambda e: self._pick_candidate("title"))
        self.title_conf_lbl = ttk.Label(name_host, text="", foreground="#49566a", wraplength=360)
        self.title_conf_lbl.pack(anchor="w", pady=(2, 6))

        r2 = ttk.Frame(name_host)
        r2.pack(fill="x", pady=2)
        r2.columnconfigure(1, weight=1)
        ttk.Label(r2, text="填报单位", width=8).grid(row=0,column=0,sticky="w")
        self.unit_entry = ttk.Entry(r2, textvariable=self.unit_var, width=16)
        self.unit_entry.grid(row=0,column=1,sticky="ew")
        unit_options = ttk.Frame(name_host)
        unit_options.pack(fill="x", pady=2)
        ttk.Label(unit_options, text="候选", width=5).pack(side="left")
        # 单位候选：OCR 候选（降序）+ 历史单位（无置信度）
        uc_cands = [c for c in group.unit_candidates if c.get("text")]
        self.unit_cb_map = self._build_candidate_map(uc_cands)
        for h in history_items:
            if h and all(h != c.get("text") for c in uc_cands):
                self.unit_cb_map.setdefault(h, f"{h}（历史）")
        self.unit_cb = ttk.Combobox(unit_options, values=list(self.unit_cb_map.values()), width=16, state="readonly")
        if self.unit_cb_map:
            self.unit_cb.set(next(iter(self.unit_cb_map.values())))
        self.unit_cb.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.unit_cb.bind("<<ComboboxSelected>>", lambda e: self._pick_candidate("unit"))
        self.unit_conf_lbl = ttk.Label(name_host, text="", foreground="#49566a", wraplength=360)
        self.unit_conf_lbl.pack(anchor="w", pady=(2, 6))
        ttk.Button(unit_options, text="查看来源", command=lambda: self._show_source("unit")).pack(side="right")
        ttk.Button(title_options, text="查看来源", command=lambda: self._show_source("title")).pack(side="right")
        ttk.Button(r1, text="确认标题", command=lambda: self._confirm_field("title")).grid(row=0,column=2,padx=(4,0))
        ttk.Button(r2, text="确认单位", command=lambda: self._confirm_field("unit")).grid(row=0,column=2,padx=(4,0))
        self.title_options, self.unit_options = title_options, unit_options
        self._options_visible = False
        title_options.pack_forget()
        unit_options.pack_forget()
        ttk.Button(name_host, text="查看候选与识别来源", command=self._toggle_evidence).pack(anchor="w", pady=(4,0))

        r3 = ttk.Frame(name_host)
        r3.pack(fill="x", pady=(4, 0))
        ttk.Label(r3, text="导出为").pack(side="left")
        self.fname_lbl = ttk.Label(r3, text="", foreground="#1a6b3c", font=("Segoe UI", 9), wraplength=330)
        self.fname_lbl.pack(side="left", padx=4)
        ttk.Button(name_host, text="确认本份名称并看下一份", command=self._confirm_name).pack(anchor="e", pady=(8, 2))
        self.title_entry.bind("<Return>", lambda e: self._confirm_name())
        self.unit_entry.bind("<Return>", lambda e: self._confirm_name())

        self.title_var.trace_add("write", lambda *a, f="title": self._on_change(f))
        self.unit_var.trace_add("write", lambda *a, f="unit": self._on_change(f))
        self._loading = False
        self._update_filename()
        self._update_confirm_badge()
        self._refresh_field_hints()
        self.bind("<Configure>", self._resize_text)

    def _resize_text(self,event):
        width = max(160,event.width-24)
        for label in (self.header_label,self.confirm_lbl,self.title_conf_lbl,self.unit_conf_lbl):
            label.configure(wraplength=width)
        self.fname_lbl.configure(wraplength=max(120,width-60))

    def _build_candidate_map(self, cands: list) -> dict[str, str]:
        """候选 → 展示标签（按置信度降序）。

        返回 {真实文本: "文本（置信 85% · 落款）"}，用 dict 保持插入顺序 =
        展示顺序；同名候选保留最高置信度的一条。
        """
        best: dict[str, dict] = {}
        for c in cands:
            t = (c.get("text") or "").strip()
            if not t:
                continue
            try:
                conf = float(c.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            if t not in best or conf > best[t]["conf"]:
                best[t] = {"conf": conf, "source": c.get("source", "")}
        ordered = sorted(best.items(), key=lambda kv: kv[1]["conf"], reverse=True)
        out: dict[str, str] = {}
        for t, info in ordered:
            label = f"{t}（置信 {info['conf']:.0%} · {info['source']}）" if info["source"] \
                else f"{t}（置信 {info['conf']:.0%}）"
            out[t] = label
        return out

    def _pick_candidate(self, field: str):
        """从下拉选择候选：把展示标签反查为真实文本填入输入框。"""
        if field == "title":
            m, cb, var = self.title_cb_map, self.title_cb, self.title_var
        else:
            m, cb, var = self.unit_cb_map, self.unit_cb, self.unit_var
        label = cb.get()
        text = next((k for k, v in m.items() if v == label), label)
        var.set(text)

    def _confirm_field(self, field):
        if self.app._busy:
            return
        var = self.title_var if field == "title" else self.unit_var
        if not var.get().strip():
            messagebox.showinfo("确认名称", "请先填写该字段。")
            return
        self._on_change(field)

    def _toggle_evidence(self):
        self._options_visible = not self._options_visible
        for frame, before in ((self.title_options,self.title_conf_lbl),(self.unit_options,self.unit_conf_lbl)):
            if self._options_visible:
                frame.pack(fill="x", pady=2, before=before)
            else:
                frame.pack_forget()

    def _confirm_name(self):
        if self.app._busy or not self.group.boundary_confirmed or self.doc.analysis_stale:
            return
        if not self.title_var.get().strip() or not self.unit_var.get().strip():
            messagebox.showinfo("补全文件名称", "请填写标题和填报单位。未找到填报单位时，请按原件核对后手动填写。")
            return
        self.app._push_undo()
        self._on_change("title")
        self._on_change("unit")
        self.app.groups._update_summary(self.doc)
        self.app.focus_next_name()

    def _conf_text(self, cands: list, field: str = "title", confirmed: bool = True) -> str:
        """字段证据行（v1.9.1 阶段四）：明确区分"已人工确认 / 自动建议 / 待确认"，
        并展示 OCR 候选来源（文头/落款/正文/标题/文本层）。"""
        if confirmed:
            if not getattr(self.group, f"{field}_manual", False):
                return "自动接受 · 仍需检查拆分" if not self.group.boundary_confirmed else "自动接受"
            if cands:
                c = cands[0]
                return f"已人工确认 · 来源 {c.get('source', '')}"
            return "已人工确认（无候选）"
        if not cands:
            return "无候选 · 待确认（需手动填写）"
        c = cands[0]
        try:
            conf = float(c.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        src = c.get("source", "")
        reason = c.get("reason", "")
        s = f"自动建议 · 置信 {conf:.0%} · 来源 {src}"
        # 正文弱证据不得冒充已确认（明确警示）
        if src == "正文":
            s += " · 弱证据，需人工核对"
        if reason:
            s += f" · {reason}"
        return s

    def _refresh_field_hints(self):
        """刷新标题/单位证据行（区分自动建议与已人工确认）。"""
        for field in ("title", "unit"):
            text = self._conf_text(getattr(self.group, f"{field}_candidates"), field,
                                  getattr(self.group, f"{field}_confirmed"))
            if not getattr(self.group, field).strip():
                text = "尚未识别，请先点击顶部“识别标题与填报单位”。" if not self.doc.naming_attempted else (
                    "未找到明确的填报单位，请核对原件后填写。" if field == "unit" else "未找到可靠标题，请按原件填写。")
            getattr(self, f"{field}_conf_lbl").configure(text=text)

    def _on_change(self, field_name: str, *a):
        """字段级确认（v1.8）：编辑哪个字段只确认哪个字段，
        另一个字段的人工待确认状态不受影响。"""
        if self._loading or self.app._busy:
            return
        if field_name == "title":
            self.group.title = self.title_var.get().strip()
            self.group.title_confirmed = bool(self.group.title)
            self.group.title_manual = True
        else:
            self.group.unit = self.unit_var.get().strip()
            self.group.unit_confirmed = bool(self.group.unit)
            self.group.unit_manual = True
        self._update_confirm_badge()
        self._refresh_field_hints()
        self.app.update_status()
        if self.app.groups._doc is self.doc:
            self.app.groups._update_summary(self.doc)
        self._update_filename()

    def _update_confirm_badge(self):
        if not self.group.need_confirm and self.group.title.strip() and self.group.unit.strip():
            self.confirm_lbl.configure(text="已确认", background="#1e8449", foreground="#ffffff")
        else:
            pending = []
            if not self.group.boundary_confirmed:
                pending.append("边界")
            if not self.group.title_confirmed or not self.group.title.strip():
                pending.append("标题")
            if not self.group.unit_confirmed or not self.group.unit.strip():
                pending.append("填报单位")
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
        # 重名冲突预检（同批导出内的文件名重复）
        dup = self.app.export_name_conflicts().get(name + ".pdf")
        if dup and dup > 1:
            self.fname_lbl.configure(text=name + ".pdf  ⚠ 重名 ×" + str(dup), foreground="#a93226")
        else:
            self.fname_lbl.configure(text=name + ".pdf", foreground="#1a6b3c")

    def _preview(self, page_no: int):
        """定位到指定页（用于查看分组首页 / 边界前后页）。"""
        if 1 <= page_no <= self.doc.page_count:
            self.app.locate_page(page_no, self.doc)

    def _show_source(self, field):
        value = getattr(self.group, field)
        candidates = getattr(self.group, f"{field}_candidates")
        c = next((c for c in candidates if c.get("text") == value), candidates[0] if candidates else {})
        if c.get("page"):
            self._preview(c["page"])
            if c.get("bbox") and c.get("image_size"):
                self.app.preview.show_evidence(c["bbox"], c["image_size"])
        if c:
            messagebox.showinfo("识别来源", f"原第 {c.get('page') or self.group.start} 页\n"
                f"来源：{c.get('source', '')}\n文字：{c.get('text', '')}\n"
                f"依据：{c.get('reason', '')}")

    def focus_pending(self):
        """定位本组首页，并让面板滚动到可视区（"下一处待确认"用）。"""
        self._preview(self.group.start)
        if self.group.boundary_confirmed and not self.doc.analysis_stale:
            entry = self.title_entry if not self.group.title_confirmed or not self.group.title.strip() else self.unit_entry
            entry.focus_set()
            entry.selection_range(0, "end")
        try:
            self.update_idletasks()
            y = self.winfo_y()
            total = max(self.master.winfo_height(), 1)
            self.update_idletasks()
            inner_h = max(self.master.winfo_height(), 1)
            area = getattr(self.app, "groups", None)
            if area is not None:
                bbox = area.canvas.bbox("all")
                if bbox and bbox[3] > 0:
                    area.canvas.yview_moveto(max(0.0, (y - 20) / bbox[3]))
        except Exception:
            pass


class GroupPanelArea(ttk.Frame):
    """右侧分组面板滚动区（含顶部"待处理问题"汇总条）。"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        # 顶部汇总条：集中展示待处理问题（不靠日志），并提供待确认项快速跳转
        self.summary = ttk.Frame(self, padding=(6, 4))
        self.summary.pack(fill="x")
        self.summary_lbl = ttk.Label(self.summary, text="", anchor="w", justify="left", wraplength=360)
        self.summary_lbl.pack(fill="x")
        summary_actions = ttk.Frame(self.summary)
        summary_actions.pack(fill="x", pady=(4, 0))
        self.next_btn = ttk.Button(summary_actions, text="下一处待确认", width=12,
                                   command=self._goto_next_pending)
        self.next_btn.pack(side="right")
        self.rename_btn = ttk.Button(summary_actions, text="重新识别待确认名称", width=18,
                                     command=lambda: self.app.rename_affected_groups())
        self.rename_btn.pack(side="right", padx=(0, 4))
        self._pending_panels: list = []
        self._pending_pages: list[int] = []
        self._pending_cursor = -1
        self._doc = None

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
        self._pending_panels = []
        self._pending_pages = []
        self._pending_cursor = -1
        self._doc = doc
        if doc is None:
            self.summary_lbl.configure(text="")
            return
        history = self.app.unit_history.items
        for gi, g in enumerate(doc.groups):
            panel = GroupPanel(self.inner, self.app, doc, gi, g, history)
            panel.pack(fill="x", padx=4, pady=4)
            self.panels.append(panel)
            def bind_wheel(widget):
                widget.bind("<MouseWheel>", self._scroll_on_wheel, add="+")
                for child in widget.winfo_children():
                    bind_wheel(child)
            bind_wheel(panel)
        if not doc.groups:
            ttk.Label(self.inner, text="尚未分组。请先运行“检测空白页”与“自动分组”。",
                      foreground="#5d6d7e").pack(pady=12)
        self._update_summary(doc)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _scroll_on_wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta/120)*2, "units")
        return "break"

    def _update_summary(self, doc):
        """汇总本文件的待处理问题（待确认字段 / 待确认边界 / 分隔页）。"""
        n_conf = [gi for gi, g in enumerate(doc.groups) if not g.title_confirmed or not g.unit_confirmed or not g.title.strip() or not g.unit.strip()]
        n_boundary = sum(not g.boundary_confirmed for g in doc.groups)
        n_sep = len(doc.separators)
        n_split = len(doc.split_starts)
        n_prop = sum(1 for p in doc.pages if getattr(p, "has_rotation_proposal", False))
        parts = [f"共 {len(doc.groups)} 组"]
        if n_boundary:
            parts.append(f"⚠ {n_boundary} 组拆分待确认")
        if n_conf:
            parts.append(f"⚠ {len(n_conf)} 组待确认命名")
        if n_prop:
            parts.append(f"⚠ {n_prop} 页有方向建议待处理")
        if n_sep:
            parts.append(f"分隔页 {n_sep}（导出排除）")
        if n_split:
            parts.append(f"拆分点 {n_split}")
        if n_conf or n_prop or n_boundary:
            self.summary_lbl.configure(
                text=" · ".join(parts), foreground="#a04000")
        else:
            self.summary_lbl.configure(text=" · ".join(parts), foreground="#1e8449")
        self._rebuild_pending()

    def _rebuild_pending(self):
        """重建待处理队列（v1.9.1 改进4）：命名待确认组 + 方向建议待处理页。

        "下一处待确认"会在这两类之间统一轮转，不再遗漏方向建议。
        """
        doc = self._doc
        self._pending_panels = []
        self._pending_pages = []
        if doc is None:
            self._pending_cursor = -1
            return
        self._pending_panels = [self.panels[gi] for gi, g in enumerate(doc.groups)
                                if (g.need_confirm or not g.title.strip() or not g.unit.strip()) and gi < len(self.panels)]
        self._pending_pages = [p.src_index for p in doc.pages
                               if getattr(p, "has_rotation_proposal", False)]
        n = len(self._pending_panels) + len(self._pending_pages)
        if n == 0:
            self._pending_cursor = -1
        elif self._pending_cursor >= n:
            self._pending_cursor = n - 1

    def _goto_next_pending(self):
        """在所有待处理项中循环跳转：先命名待确认组，再方向建议页，循环推进。"""
        self._rebuild_pending()
        total = len(self._pending_panels) + len(self._pending_pages)
        if total == 0:
            self.app.log("没有待确认的事项。")
            return
        self._pending_cursor = (self._pending_cursor + 1) % total
        i = self._pending_cursor
        if i < len(self._pending_panels):
            panel = self._pending_panels[i]
            panel.focus_pending()
            self.app.log(
                f"定位待确认命名：文档 {panel.gi + 1}"
                f"（第 {panel.group.start}-{panel.group.end} 页）")
        else:
            pno = self._pending_pages[i - len(self._pending_panels)]
            self.app.on_page_select(pno, preview=True)
            self.app.thumbs.ensure_visible(pno)
            page = self._doc.pages[pno - 1]
            deg = int(page.rotation_proposal) % 360
            self.app.log(f"定位方向建议：第 {pno} 页（建议 {deg}°，请在预览上方“识别方向”中采用或忽略）")

    def _refresh_pending(self):
        self._rebuild_pending()


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


class ExportSummaryDialog(tk.Toplevel):
    """导出前检查摘要（v1.9.1 改进3）：把原来的一行 askyesno 换成**可勾选清单**。

    - 顶部展示统计（份数 / 已确认 / 排除页）；
    - 中部是「导出清单」：每组一行（勾选框 + 序号 + 页码范围 + 文件名 + 状态），
      用户可取消勾选个别组（例如重名或不确定的组）后再导出；
    - 底部区分两类问题：
        * 字段级：空标题/空单位/待确认 —— 返回确认或取消勾选；
        * 完整性（不可放宽）：覆盖率/异常排除/保真 —— 只读展示，无继续按钮。
    结果写入 self.result：{"items": [...], "strict": bool, "confirmed": bool}。
    """

    def __init__(self, master, docs, stats, field_issues, integrity_issues,
                 name_of, integrity_blocked=False):
        super().__init__(master)
        self.title("导出前检查")
        self.transient(master)
        self.resizable(True, True)
        self.geometry("640x560")
        self.result = {"items": [], "strict": True, "confirmed": False}
        self._docs = docs
        self._name_of = name_of
        self._integrity_blocked = integrity_blocked
        self._vars: list[tuple[object, tk.BooleanVar]] = []

        head = ttk.Frame(self, padding=(12, 10, 12, 4))
        head.pack(fill="x")
        ttk.Label(head, text="导出前检查摘要", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            head,
            text=(f"预计导出 {stats['n_groups']} 份 PDF　·　名称已确认 {stats['n_confirmed']} 份"
                  + (f"，待确认 {stats['n_pending']} 份" if stats["n_pending"] else "（全部已确认）")),
            foreground="#333333",
        ).pack(anchor="w", pady=(2, 0))
        ex = stats.get("excluded") or []
        ttk.Label(
            head,
            text=("排除原始页：" + "、".join(ex[:8]) + ("……" if len(ex) > 8 else "")) if ex
            else "无被排除的原始页",
            foreground="#a93226" if ex else "#1e8449",
            wraplength=600, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        # -- 清单表 ----------------------------------------------------------
        ttk.Label(self, text="导出清单（取消勾选可跳过该组）：",
                  padding=(12, 6, 12, 0)).pack(anchor="w")
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=12, pady=(2, 6))
        cols = ("on", "idx", "pages", "name", "state")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=9)
        self.tree.heading("on", text="导出")
        self.tree.heading("idx", text="#")
        self.tree.heading("pages", text="页码范围")
        self.tree.heading("name", text="文件名")
        self.tree.heading("state", text="状态")
        self.tree.column("on", width=48, anchor="center", stretch=False)
        self.tree.column("idx", width=40, anchor="center", stretch=False)
        self.tree.column("pages", width=110, anchor="center", stretch=False)
        self.tree.column("name", width=320, anchor="w")
        self.tree.column("state", width=110, anchor="w", stretch=False)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_click, add="+")

        idx = 0
        for di, doc in enumerate(docs):
            for gi, g in enumerate(doc.groups):
                idx += 1
                name = self._name_of(doc, gi, g)
                state = "已确认" if not g.need_confirm else "待确认"
                if not g.title.strip() or not g.unit.strip():
                    state = "字段缺失"
                if stats.get("dup", {}).get(name):
                    state = "重名 ×%d" % stats["dup"][name]
                mark = "☑" if state in ("已确认", "待确认") else "☑"
                var = tk.BooleanVar(value=True)
                self._vars.append(((di, gi), var))
                self.tree.insert("", "end", iid=str(idx), values=(
                    mark, idx, f"{g.start}-{g.end}", name, state,
                ))
        self._refresh_marks()

        # -- 问题区 ----------------------------------------------------------
        info = tk.Text(self, height=6, wrap="word", font=("Segoe UI", 9))
        info.pack(fill="x", padx=12)
        lines = []
        if integrity_issues:
            lines.append("【页面完整性】以下问题不可放宽，正式导出被阻断：")
            lines += ["  · " + m for m in integrity_issues[:10]]
            if len(integrity_issues) > 10:
                lines.append("  ……")
        if field_issues:
            lines.append("【字段/命名】请确认名称，或取消勾选尚未完成的分组：")
            lines += ["  · " + m for m in field_issues[:10]]
            if len(field_issues) > 10:
                lines.append("  ……")
        if not lines:
            lines.append("未发现阻断项，可直接导出。")
        info.insert("1.0", "\n".join(lines))
        info.configure(state="disabled")

        # -- 按钮 ------------------------------------------------------------
        btns = ttk.Frame(self, padding=(12, 6, 12, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="取消", width=10, command=self._cancel).pack(side="right")
        if integrity_blocked:
            ttk.Button(btns, text="返回修改", width=12, command=self._cancel).pack(side="right", padx=6)
            ttk.Button(btns, text="关闭", width=10, command=self._cancel).pack_forget()
        else:
            ok = ttk.Button(btns, text="按清单导出", width=14, command=self._confirm_strict)
            ok.pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        self.grab_set()

    def _current_name(self, key):
        di, gi = key
        doc = self._docs[di]
        return self._name_of(doc, gi, doc.groups[gi])

    def _on_click(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid:
            return
        if self.tree.identify_column(e.x) != "#1":
            return
        self._toggle(iid)

    def _toggle(self, iid):
        i = int(iid) - 1
        if 0 <= i < len(self._vars):
            _, var = self._vars[i]
            var.set(not var.get())
            self._refresh_marks()

    def _refresh_marks(self):
        for i, (key, var) in enumerate(self._vars):
            iid = str(i + 1)
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "☑" if var.get() else "☐"
            self.tree.item(iid, values=vals)

    def _selected_items(self):
        out = []
        for key, var in self._vars:
            if var.get():
                out.append(key)
        return out

    def _confirm_strict(self):
        items = self._selected_items()
        if not items:
            messagebox.showwarning("导出", "请至少勾选一个分组。", parent=self)
            return
        for di, gi in items:
            g = self._docs[di].groups[gi]
            if g.need_confirm or not g.title.strip() or not g.unit.strip():
                messagebox.showwarning("导出", "勾选的分组仍有待确认项或名称缺失，请返回确认后导出。", parent=self)
                return
        self.result = {"items": items, "strict": True, "confirmed": True}
        self.destroy()

    def _cancel(self):
        self.result = {"items": [], "strict": True, "confirmed": False}
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result


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
