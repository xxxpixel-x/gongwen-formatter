"""排版预览：在 tk.Text 里按格式要求近似画出排好版的文档。

只是预览，以生成的 Word 文件为准：
- 不分页，整篇连续显示；页宽按 A4 缩放到窗口宽度，页边距、字号、行距、缩进按同一比例
- tk 不支持两端对齐，按左对齐显示
- 字体用本机已安装的；没装的（如方正小标宋、仿宋_GB2312）用相近的字体代替
"""
from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from .classify import Item, Role, insert_blanks
from .spec import Spec, Style

PT_PER_MM = 72 / 25.4
PAGE_W_MM = 210

PAGE_BG, DESK_BG = "#ffffff", "#e6e6e6"
YELLOW, GREEN = "#fff1a8", "#dff3dc"
SELECT_BG = "#eef4ff"
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
        self.text = tk.Text(self, wrap="char", relief="flat", borderwidth=0, highlightthickness=0,
                            background=PAGE_BG, cursor="arrow", padx=0, pady=0)
        sb = tk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set, state="disabled")
        self.text.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._fonts: dict[tuple, tkfont.Font] = {}
        self._families: set[str] | None = None
        self._family_cache: dict[str, str] = {}
        self._args: tuple | None = None
        self._width = 0
        self._resize_job = None
        self.text.bind("<Configure>", self._on_resize)

    # ---------- 对外 ----------

    def show(self, items: list[Item], spec: Spec, selected: int | None = None, keep_scroll: bool = True):
        self._args = (items, spec, selected)
        self._draw(keep_scroll)

    def clear(self):
        self._args = None
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
            self._para_tag(ptag, it, st, k, left, right, selected == i)

            if it.raw is not None:   # 表格：原样保留，预览里只占个位置
                t.insert("end", "［表格：原样保留，预览中不显示］", (ptag, self._font_tag("宋体", round(10.5 * k), False)))
            else:
                body = spec.styles[Role.BODY]
                lead = it.lead_len if it.role in (Role.H2, Role.H3, Role.H4, Role.H5) else 0
                parts = [(it.text[:lead], st), (it.text[lead:], body)] if 0 < lead < len(it.text) \
                    else [(it.text, st)]
                for chunk, cst in parts:
                    self._insert_runs(chunk, cst, latin, k, ptag)
            t.insert("end", "\n", (ptag,))
            if i is not None and it.raw is None:
                t.tag_bind(ptag, "<Button-1>", lambda e, i=i: self.on_click(i))

        t.insert("end", "\n", ("top",))   # 下方留白
        t.configure(state="disabled")
        if keep_scroll:
            t.yview_moveto(y)

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
