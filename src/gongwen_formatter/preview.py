"""排版预览：在 tk.Text 里按格式要求近似画出排好版的文档。

只是预览，以生成的 Word 文件为准：
- 不分页，整篇连续显示；页宽按 A4 缩放到窗口宽度，页边距、字号、行距、缩进按同一比例
- tk 不支持两端对齐，按左对齐显示
- 字体用本机已安装的；没装的（如方正小标宋、仿宋_GB2312）用相近的字体代替
- 表格按原稿的列宽、合并单元格、字体、底纹画出，单元格里的数字英文也用中文字体显示
"""
from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from .classify import Item, Role, insert_blanks
from .spec import Spec, Style
from .table import alignment, border_color, cells, column_widths, has_borders

PT_PER_MM = 72 / 25.4
PAGE_W_MM = 210

PAGE_BG, DESK_BG, PAGE_EDGE = "#ffffff", "#e9ecef", "#cfd4da"
MAX_PAGE_PX = 820   # 页面最宽多少像素（按 96 DPI，高分屏自动放大）
GAP = 16
YELLOW, GREEN = "#fff1a8", "#dff3dc"
SELECT_BG = "#eef4ff"
TABLE_LINE, TABLE_NO_LINE = "#000000", "#d5d9de"   # 原稿表格没有边框时用浅灰线示意
JUSTIFY = {"both": "left", "left": "left", "center": "center", "right": "right"}

# 数字、英文、半角符号用西文字体显示（与 render 里 ascii/hAnsi 字体一致）
_LATIN = re.compile(r"[\x21-\x7e]+(?: +[\x21-\x7e]+)*")

# 没装的字体用相近的代替：按关键字依次找本机有的
_FALLBACK = [
    ("小标宋", ["方正小标宋简体", "方正小标宋_GBK", "华文中宋", "宋体"]),
    ("仿宋", ["仿宋_GB2312", "仿宋", "FangSong", "华文仿宋"]),
    ("楷", ["楷体_GB2312", "楷体", "KaiTi", "华文楷体"]),
    ("黑", ["黑体", "SimHei", "微软雅黑"]),
    ("宋", ["宋体", "SimSun", "华文宋体"]),
]


class Preview(tk.Frame):
    """on_click(i)：用户点了第 i 段（items 里的下标）。"""

    def __init__(self, master, on_click: Callable[[int], None]):
        super().__init__(master, background=DESK_BG)
        self.on_click = on_click
        # 页面居中放在灰色“桌面”上，宽度有上限：窗口很宽时字也不会被放得过大
        self.text = tk.Text(self, wrap="char", relief="flat", borderwidth=0, highlightthickness=1,
                            highlightbackground=PAGE_EDGE, highlightcolor=PAGE_EDGE,
                            background=PAGE_BG, cursor="arrow", padx=0, pady=0)
        self._sb = sb = tk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set, state="disabled")
        self.text.grid(row=0, column=0, sticky="nsew", pady=GAP)
        sb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._max_page = round(MAX_PAGE_PX * self.winfo_fpixels("1i") / 96)
        self._page_pad = -1
        self.bind("<Configure>", self._fit_page)

        # 还没有原稿时盖在预览上的提示区，内容由界面填
        self.placeholder = tk.Frame(self, background=PAGE_BG)
        self.placeholder.place(relx=0.5, rely=0.42, anchor="center")

        self._fonts: dict[tuple, tkfont.Font] = {}
        self._families: set[str] | None = None
        self._family_cache: dict[str, str] = {}
        self._args: tuple | None = None
        self._width = 0
        self._resize_job = None
        self._windows: list[tk.Widget] = []   # 嵌在页面里的表格，重画前要销毁
        self.text.bind("<Configure>", self._on_resize)

    # ---------- 对外 ----------

    def show(self, items: list[Item], spec: Spec, selected: int | None = None, keep_scroll: bool = True):
        self.placeholder.place_forget()
        self._args = (items, spec, selected)
        self._draw(keep_scroll)

    def _fit_page(self, event):
        avail = event.width - self._sb.winfo_reqwidth()
        pad = max(GAP, (avail - self._max_page) // 2)
        if pad != self._page_pad:
            self._page_pad = pad
            self.text.grid_configure(padx=pad)

    def clear(self):
        self._args = None
        self._destroy_windows()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def see(self, i: int):
        """把第 i 段滚到可见区域（尽量放在上方三分之一处，方便看到上下文）。"""
        first = self.text.tag_ranges(f"p{i}")
        if not first:
            return
        self.text.see(first[0])
        top, bottom = self.text.yview()
        line = float(self.text.index(first[0]).split(".")[0])
        total = float(self.text.index("end").split(".")[0])
        frac = max(0.0, line / total - (bottom - top) / 3)
        self.text.yview_moveto(frac)

    # ---------- 字体 ----------

    def _family(self, name: str) -> str:
        if name in self._family_cache:
            return self._family_cache[name]
        if self._families is None:
            self._families = set(tkfont.families(self))
        found = name if name in self._families else None
        if found is None:
            for key, candidates in _FALLBACK:
                if key in name:
                    found = next((c for c in candidates if c in self._families), None)
                    break
        self._family_cache[name] = found or name
        return self._family_cache[name]

    def _font(self, family: str, px: int, bold: bool) -> tkfont.Font:
        key = (self._family(family), px, bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(self, family=key[0], size=-max(px, 4),
                                           weight="bold" if bold else "normal")
        return self._fonts[key]

    def _font_tag(self, family: str, px: int, bold: bool) -> str:
        tag = f"f|{family}|{px}|{int(bold)}"
        self.text.tag_configure(tag, font=self._font(family, px, bold))
        return tag

    # ---------- 绘制 ----------

    def _on_resize(self, event):
        if abs(event.width - self._width) < 4:
            return
        self._width = event.width
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, lambda: self._draw(True))

    def _draw(self, keep_scroll: bool):
        self._resize_job = None
        if not self._args:
            return
        items, spec, selected = self._args
        t = self.text
        width = t.winfo_width()
        if width < 100:
            return
        k = width / (PAGE_W_MM * PT_PER_MM)          # 每磅多少像素
        top, _, left, right = (m * PT_PER_MM * k for m in spec.margins_mm)

        y = t.yview()[0]
        self._destroy_windows()
        t.configure(state="normal")
        t.delete("1.0", "end")
        for tag in t.tag_names():
            if tag != "sel":
                t.tag_delete(tag)

        t.insert("end", "\n", ("top",))   # 上页边距
        t.tag_configure("top", font=self._font("宋体", 2, False), spacing1=round(top))

        index = {id(it): i for i, it in enumerate(items)}
        latin = spec.latin_font
        for it in insert_blanks(items):
            i = index.get(id(it))
            st = spec.styles[it.role]
            ptag = f"p{i}" if i is not None else f"b{t.index('end')}"
            if it.raw is not None:
                self._table(it.raw, spec, k, left, right, ptag)
                t.insert("end", "\n", (ptag,))
                continue
            self._para_tag(ptag, it, st, k, left, right, selected == i)

            body = spec.styles[Role.BODY]
            lead = it.lead_len if it.role in (Role.H2, Role.H3, Role.H4, Role.H5) else 0
            parts = [(it.text[:lead], st), (it.text[lead:], body)] if 0 < lead < len(it.text) \
                else [(it.text, st)]
            for chunk, cst in parts:
                self._insert_runs(chunk, cst, latin, k, ptag)
            t.insert("end", "\n", (ptag,))
            if i is not None:
                t.tag_bind(ptag, "<Button-1>", lambda e, i=i: self.on_click(i))

        t.insert("end", "\n", ("top",))   # 下方留白
        t.configure(state="disabled")
        if keep_scroll:
            t.yview_moveto(y)

    def _destroy_windows(self):
        for w in self._windows:
            w.destroy()
        self._windows = []

    def _table(self, tbl, spec: Spec, k: float, left: float, right: float, ptag: str):
        """用一格一个 Label 画出表格：列宽按原稿比例，放不下时缩到版心宽度（与生成的文件一致）。"""
        t = self.text
        widths = column_widths(tbl) or [1]
        page_px = t.winfo_width() - left - right
        px_per_twip = k / 20
        total = sum(widths) * px_per_twip
        scale = min(1.0, page_px / total) if total else 1.0
        col_px = [max(8, round(w * px_per_twip * scale)) for w in widths]

        color = border_color(tbl)
        line = (f"#{color}" if color else TABLE_LINE) if has_borders(tbl) else TABLE_NO_LINE
        frame = tk.Frame(t, background=line, cursor="arrow")
        pad = max(2, round(4 * k))
        grid_cells = cells(tbl)
        n_rows = max((c.row + c.rowspan for c in grid_cells), default=0)
        for c in grid_cells:
            # 表格保持原稿的格式；格式要求里写到的项才用要求里的
            role = Role.TABLE_HEAD if c.head else Role.TABLE
            st, fields = spec.styles[role], spec.table_fields.get(role, set())
            font = st.font if "font" in fields else (c.font or "宋体")
            size = st.size if "size" in fields else (c.size or 10.5)
            bold = st.bold if "bold" in fields else c.bold
            align = st.align if "align" in fields else c.align
            span_px = sum(col_px[c.col:c.col + c.colspan]) or col_px[-1]
            anchor, justify = {"center": ("center", "center"), "right": ("e", "right")}.get(align, ("w", "left"))
            label = tk.Label(frame, text=c.text, background=f"#{c.fill}" if c.fill else PAGE_BG,
                             foreground=f"#{c.color}" if c.color else "#000000",
                             font=self._font(font, round(size * k), bold),
                             anchor=anchor, justify=justify, padx=pad, pady=pad,
                             wraplength=max(10, span_px - 2 * pad - 1))
            label.grid(row=c.row, column=c.col, rowspan=c.rowspan, columnspan=c.colspan,
                       sticky="nsew", padx=(1, 0), pady=(1, 0))
        for col, w in enumerate(col_px):
            frame.grid_columnconfigure(col, minsize=w)
        frame.grid_columnconfigure(len(col_px), minsize=1)   # 最右、最下的边线
        frame.grid_rowconfigure(n_rows, minsize=1)

        # 滚轮在表格上也能滚动页面
        def wheel(e):
            t.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
            return "break"
        for w in (frame, *frame.winfo_children()):
            w.bind("<MouseWheel>", wheel)

        t.tag_configure(ptag, lmargin1=round(left), lmargin2=round(left), rmargin=round(right),
                        justify=alignment(tbl), spacing1=pad, spacing3=pad)
        start = t.index("end-1c")
        t.window_create("end", window=frame)
        t.tag_add(ptag, start, "end-1c")
        self._windows.append(frame)

    def _insert_runs(self, text: str, st: Style, latin: str, k: float, ptag: str):
        px = round(st.size * k)
        cjk = self._font_tag(st.font, px, st.bold)
        lat = self._font_tag(latin, px, st.bold)
        pos = 0
        for m in _LATIN.finditer(text):
            if m.start() > pos:
                self.text.insert("end", text[pos:m.start()], (ptag, cjk))
            self.text.insert("end", m.group(0), (ptag, lat))
            pos = m.end()
        if pos < len(text):
            self.text.insert("end", text[pos:], (ptag, cjk))

    def _para_tag(self, tag: str, it: Item, st: Style, k: float, left: float, right: float, selected: bool):
        """段落格式：缩进、对齐、行距、段前段后，以及“拿不准”的底色。"""
        px = round(st.size * k)
        linespace = self._font(st.font, px, st.bold).metrics("linespace")
        target = st.line * k if st.line else linespace * st.line_multiple
        extra = max(0.0, target - linespace)
        opts = dict(
            lmargin1=round(left + st.first_indent * st.size * k),
            lmargin2=round(left),
            rmargin=round(right + st.right_indent * st.size * k),
            justify=JUSTIFY.get(st.align, "left"),
            spacing1=round(extra / 2 + st.before * k),
            spacing2=round(extra),
            spacing3=round(extra - extra / 2 + st.after * k),
        )
        if it.confidence == "low":
            opts["background"] = GREEN if it.confirmed else YELLOW
        if selected:   # 当前选中的段落：实线框（没有底色时再加浅蓝底）
            opts.update(relief="solid", borderwidth=2)
            opts.setdefault("background", SELECT_BG)
        self.text.tag_configure(tag, **opts)
