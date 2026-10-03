"""图形界面（tkinter，Python 自带，打包后体积小）。

左边：① 格式要求 → 下方表格实时显示“程序理解成了什么”
右边：② 选择原稿 → 只列出程序不确定的段落（黄色卡片），每张卡片上直接选正确类型
底部：③ 生成排版文件

布局全部用 grid + 权重，窗口缩放、高分屏下都不会遮挡文字。
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .classify import ROLE_LABELS, lead_len
from .extract import extract
from .fonts import installed_fonts, missing_fonts
from .render import read_document, render
from .spec import DEFAULT_TEXT, DISPLAY_ROLES, describe, fonts_used, parse

APP_NAME = "公文格式整理器"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "gongwen-formatter"
REQ_FILE = CONFIG_DIR / "requirements.txt"

SPEC_COLUMNS = ["字体", "字号", "加粗", "对齐", "缩进", "行距"]
LABEL_TO_ROLE = {ROLE_LABELS[r]: r for r in DISPLAY_ROLES}

YELLOW, GREEN, WHITE, GREY = "#fff4c2", "#e3f4e1", "#ffffff", "#6b6b6b"

HINT_SHORT = "每行写一类段落：先写类型，再写格式。例：一级标题（黑体，三号，顶格）"
HINT_FULL = (
    "也可以点“从模板读取…”，选一份已经排好版的公文，程序会自动写出它的格式要求。\n\n"
    "每行写一类段落，先写类型，再写格式。\n\n"
    "类型：标题、副标题、一级标题、二级标题、三级标题、四级标题、五级标题、正文、附件、附件名称、落款、日期\n"
    "（编号依次为 一、→（一）→1.→（1）→1）；四、五级标题没写时与正文相同）\n\n"
    "格式：字体（仿宋_GB2312、黑体……）、字号（三号、小四、16磅）、加粗、"
    "对齐（居中、两端对齐、右对齐）、缩进（首行缩进2字符、顶格、右缩进1字符）、"
    "行距（行距固定值28磅、1.5倍行距）、段前段后（段前0.5行）\n\n"
    "另外还可以写：\n英文、数字：Times New Roman\n页边距：上37毫米，下35毫米，左28毫米，右26毫米\n"
    "页眉1.5厘米，页脚2.8厘米\n\n"
    "没写到的内容按默认要求（规范图片）处理。"
)


def auto_wrap(label: tk.Widget, padding: int = 8):
    """让 Label 的换行宽度跟着自身宽度走，窗口变窄时文字自动换行而不是被截断。"""
    label.bind("<Configure>", lambda e: label.configure(wraplength=max(e.width - padding, 50)))


class ScrollFrame(ttk.Frame):
    """可以上下滚动的容器：内容放在 self.inner 里。"""

    def __init__(self, master, bg=WHITE):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=bg, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, background=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        # 鼠标停在区域上时滚轮才生效，不影响别处
        self.canvas.bind("<Enter>", lambda e: self._wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._wheel(False))

    def _wheel(self, on: bool):
        if on:
            self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
            self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
            self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
        else:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.unbind_all(seq)

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self._set_icon()
        self.src_path: str | None = None
        self.items = []
        self.read_warnings: list[str] = []
        self.fonts: set[str] | None = None
        self._fonts_ready = False
        self._parse_job = None
        self.show_all = tk.BooleanVar(value=False)

        self._init_style()
        self._build()
        self._size_window()
        self.req_text.insert("1.0", self._load_requirements())
        self.req_text.edit_modified(False)
        self._reparse()
        self._start_font_scan()

    # ---------- 外观 ----------

    def _set_icon(self):
        try:
            from .icon_data import ICON_PNG
            self._icon = tk.PhotoImage(data="".join(ICON_PNG))
            self.iconphoto(True, self._icon)
        except Exception:
            pass

    def _init_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        family = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"
        self.f_base = tkfont.Font(family=family, size=10)
        self.f_small = tkfont.Font(family=family, size=9)
        self.f_bold = tkfont.Font(family=family, size=10, weight="bold")
        self.f_step = tkfont.Font(family=family, size=12, weight="bold")
        self.f_go = tkfont.Font(family=family, size=12, weight="bold")
        self.option_add("*Font", self.f_base)
        self.option_add("*TCombobox*Listbox.font", self.f_base)
        # 行高按字体实际高度算：高分屏（150%、200% 缩放）下也不会挤压文字
        line = self.f_base.metrics("linespace")
        style.configure("Treeview", rowheight=line + 8, font=self.f_base)
        style.configure("Treeview.Heading", font=self.f_bold)
        style.configure("Step.TLabel", font=self.f_step)
        style.configure("Hint.TLabel", foreground=GREY, font=self.f_small)
        style.configure("Warn.TLabel", foreground="#c0392b")
        style.configure("Info.TLabel", foreground="#9a6700")
        style.configure("Go.TButton", font=self.f_go, padding=(16, 8))

    def _size_window(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(sw * 0.8), int(sh * 0.8)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
        self.minsize(int(sw * 0.5), int(sh * 0.5))
        if sys.platform == "win32":
            self.state("zoomed")

    # ---------- 布局 ----------

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))
        left = ttk.Frame(panes, padding=(4, 4, 10, 4))
        right = ttk.Frame(panes, padding=(10, 4, 4, 4))
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        # 初始左右各占一半（否则左边内容多，会把右边挤得很窄）
        self.after(50, lambda: panes.sashpos(0, panes.winfo_width() // 2))
        self.bind("<Map>", lambda e: e.widget is self and self.after(
            50, lambda: panes.sashpos(0, panes.winfo_width() // 2)), add="+")
        self._build_left(left)
        self._build_right(right)

        bottom = ttk.Frame(self, padding=10)
        bottom.grid(row=1, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        self.status = ttk.Label(bottom, style="Hint.TLabel")
        self.status.grid(row=0, column=0, sticky="ew")
        auto_wrap(self.status)
        self.go_btn = ttk.Button(bottom, text="③ 生成排版文件", style="Go.TButton",
                                 command=self._generate, state="disabled")
        self.go_btn.grid(row=0, column=1, sticky="e")

    def _build_left(self, left):
        left.columnconfigure(0, weight=1)

        head = ttk.Frame(left)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="① 格式要求", style="Step.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(head, text="从模板读取…", command=self._read_template).grid(row=0, column=2, sticky="e")
        ttk.Button(head, text="写法说明", command=lambda: messagebox.showinfo("格式要求的写法", HINT_FULL)
                   ).grid(row=0, column=3, sticky="e", padx=(6, 0))
        hint = ttk.Label(left, text=HINT_SHORT, style="Hint.TLabel")
        hint.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        auto_wrap(hint)

        box = ttk.Frame(left)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.req_text = tk.Text(box, height=8, wrap="word", undo=True, relief="solid", borderwidth=1,
                                padx=6, pady=4)
        req_sb = ttk.Scrollbar(box, orient="vertical", command=self.req_text.yview)
        self.req_text.configure(yscrollcommand=req_sb.set)
        self.req_text.grid(row=0, column=0, sticky="nsew")
        req_sb.grid(row=0, column=1, sticky="ns")
        self.req_text.bind("<<Modified>>", self._on_req_change)
        left.rowconfigure(2, weight=2)

        btns = ttk.Frame(left)
        btns.grid(row=3, column=0, sticky="w", pady=6)
        ttk.Button(btns, text="恢复默认要求", command=self._reset_requirements).pack(side="left")
        ttk.Button(btns, text="另存txt…", command=self._export_requirements).pack(side="left", padx=6)

        self.spec_msg = ttk.Label(left, style="Warn.TLabel")
        self.spec_msg.grid(row=4, column=0, sticky="ew")
        auto_wrap(self.spec_msg)

        ttk.Label(left, text="程序理解的格式（核对一下）", font=self.f_bold).grid(
            row=5, column=0, sticky="w", pady=(6, 2))
        tree_box = ttk.Frame(left)
        tree_box.grid(row=6, column=0, sticky="nsew")
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        left.rowconfigure(6, weight=3)
        self.spec_tree = ttk.Treeview(tree_box, columns=SPEC_COLUMNS, height=5)
        self.spec_tree.heading("#0", text="段落类型")
        em = self.f_base.measure("中")
        self.spec_tree.column("#0", width=int(em * 6.5), minwidth=int(em * 6.5), stretch=False)
        for col, n in zip(SPEC_COLUMNS, (8, 3, 2.5, 4.5, 8, 6)):
            self.spec_tree.heading(col, text=col)
            self.spec_tree.column(col, width=int(em * n), minwidth=em * 2, stretch=True)
        tsb = ttk.Scrollbar(tree_box, orient="vertical", command=self.spec_tree.yview)
        hsb = ttk.Scrollbar(tree_box, orient="horizontal", command=self.spec_tree.xview)
        self.spec_tree.configure(yscrollcommand=tsb.set, xscrollcommand=hsb.set)
        self.spec_tree.grid(row=0, column=0, sticky="nsew")
        tsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

    def _build_right(self, right):
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        head = ttk.Frame(right)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(2, weight=1)
        ttk.Label(head, text="② 原稿", style="Step.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(head, text="选择 Word 文件…", command=self._choose_file).grid(row=0, column=1, padx=10)
        self.file_label = ttk.Label(head, text="尚未选择（支持 .docx）", style="Hint.TLabel")
        self.file_label.grid(row=0, column=2, sticky="ew")

        self.doc_msg = ttk.Label(right, text="选择文件后，这里会列出程序拿不准的段落，请逐个确认。",
                                 style="Hint.TLabel")
        self.doc_msg.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        auto_wrap(self.doc_msg)

        ttk.Checkbutton(right, text="显示全部段落（发现其他段落排错时再用）",
                        variable=self.show_all, command=self._fill_cards).grid(row=2, column=0, sticky="w")

        self.cards = ScrollFrame(right)
        self.cards.grid(row=3, column=0, sticky="nsew", pady=(6, 0))

    # ---------- 格式要求 ----------

    def _load_requirements(self) -> str:
        try:
            return REQ_FILE.read_text(encoding="utf-8")
        except OSError:
            return DEFAULT_TEXT

    def _save_requirements(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            REQ_FILE.write_text(self._req(), encoding="utf-8")
        except OSError:
            pass

    def _req(self) -> str:
        return self.req_text.get("1.0", "end-1c")

    def _on_req_change(self, _event=None):
        if not self.req_text.edit_modified():
            return
        self.req_text.edit_modified(False)
        if self._parse_job:
            self.after_cancel(self._parse_job)
        self._parse_job = self.after(400, self._reparse)

    def _reparse(self):
        self._parse_job = None
        self.spec = parse(self._req())
        self.spec_tree.delete(*self.spec_tree.get_children())
        for role in DISPLAY_ROLES:
            d = describe(self.spec.styles[role])
            self.spec_tree.insert("", "end", text=ROLE_LABELS[role], values=[d[c] for c in SPEC_COLUMNS])
        self.spec_tree.insert("", "end", text="英文数字", values=[self.spec.latin_font, "", "", "", "", ""])
        t, b, l, r = self.spec.margins_mm
        self.spec_tree.insert("", "end", text="页边距",
                              values=[f"上{t:g} 下{b:g}", "", "", "", f"左{l:g} 右{r:g}（毫米）", ""])
        h, f = self.spec.header_mm, self.spec.footer_mm
        self.spec_tree.insert("", "end", text="页眉页脚", values=[
            f"页眉{h:g}" if h is not None else "页眉默认", "", "", "",
            f"页脚{f:g}（毫米）" if f is not None else "页脚默认", ""])
        self._update_spec_msg()
        self._save_requirements()

    def _update_spec_msg(self):
        msgs = list(self.spec.problems)
        if self._fonts_ready:
            missing = missing_fonts(fonts_used(self.spec), self.fonts)
            if missing:
                msgs.append("这台电脑没有安装：" + "、".join(missing)
                            + "。文件照样能生成，在装有这些字体的电脑上打开就会正常显示。")
        elif sys.platform == "win32":
            msgs.append("正在检查字体…")
        self.spec_msg.configure(text="\n".join(msgs),
                                style="Warn.TLabel" if self.spec.problems else "Info.TLabel")

    def _start_font_scan(self):
        """读取字体文件要一两秒，放到后台线程，界面不卡。"""
        result = {}

        def work():
            try:
                result["fonts"] = installed_fonts()
            except Exception:
                result["fonts"] = None

        def poll():
            if t.is_alive():
                self.after(200, poll)
            else:
                self.fonts, self._fonts_ready = result.get("fonts"), True
                self._update_spec_msg()

        t = threading.Thread(target=work, daemon=True)
        t.start()
        poll()

    def _read_template(self):
        """选一份已经排好版的公文，把它的格式写成要求文字填进输入框，供核对修改。"""
        path = filedialog.askopenfilename(title="选择一份已经排好版的公文（模板）",
                                          filetypes=[("Word 文档", "*.docx")])
        if not path:
            return
        try:
            result = extract(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"读取模板失败：{e}")
            return
        current = self._req().strip()
        if current and current != DEFAULT_TEXT.strip() and not messagebox.askyesno(
                APP_NAME, "用模板的格式替换输入框里现在的要求吗？\n（想保留现在的要求，可以先点“另存txt…”）"):
            return
        self.req_text.delete("1.0", "end")
        self.req_text.insert("1.0", result.text)
        messagebox.showinfo(APP_NAME, f"已从模板读取 {len(result.found)} 类段落的格式，填进了“格式要求”。\n\n"
                            "以 # 开头的行是说明，不影响排版。请对照下方“程序理解的格式”核对一遍，"
                            "需要的话直接在输入框里改。")

    def _reset_requirements(self):
        if messagebox.askyesno(APP_NAME, "恢复成默认格式要求（规范图片）？当前内容会被覆盖。"):
            self.req_text.delete("1.0", "end")
            self.req_text.insert("1.0", DEFAULT_TEXT)

    def _export_requirements(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="格式要求.txt",
                                            filetypes=[("文本文件", "*.txt")])
        if path:
            Path(path).write_text(self._req(), encoding="utf-8")

    # ---------- 原稿：只列出拿不准的段落 ----------

    def _choose_file(self):
        path = filedialog.askopenfilename(filetypes=[("Word 文档", "*.docx")])
        if not path:
            return
        try:
            result = read_document(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"读取失败：{e}\n\n如果是 .doc 或 .wps 文件，请先在 Word/WPS 里另存为 .docx。")
            return
        self.src_path, self.items, self.read_warnings = path, result.items, result.warnings
        for it in self.items:
            it.confirmed = it.confidence != "low"
        self.file_label.configure(text=Path(path).name)
        self.go_btn.configure(state="normal" if self.items else "disabled")
        self._fill_cards()

    def _pending(self) -> int:
        return sum(not getattr(it, "confirmed", True) for it in self.items)

    def _refresh_summary(self):
        total, pending = len(self.items), self._pending()
        if not self.items:
            return
        if pending:
            text = (f"共 {total} 段，有 {pending} 段程序拿不准（黄色卡片）。"
                    "看一下每张卡片：识别对了就点“✓ 对的”，不对就在下拉框里选正确的类型。")
        else:
            text = f"共 {total} 段，全部识别完成，可以直接生成。"
        self.doc_msg.configure(text="\n".join([text] + self.read_warnings))
        self.status.configure(text="")

    def _fill_cards(self):
        self.cards.clear()
        if not self.items:
            return
        idxs = [i for i, it in enumerate(self.items)
                if it.raw is None and (self.show_all.get() or it.confidence == "low")]
        if not idxs:
            tk.Label(self.cards.inner, text="✓ 所有段落都识别好了，不需要确认。", bg=WHITE,
                     fg="#2e7d32", font=self.f_bold, pady=20).pack(fill="x")
        for i in idxs:
            self._make_card(i)
        self._refresh_summary()

    def _make_card(self, i: int):
        item = self.items[i]
        bg = WHITE if item.confidence != "low" else (GREEN if item.confirmed else YELLOW)
        card = tk.Frame(self.cards.inner, bg=bg, highlightbackground="#d0d0d0", highlightthickness=1,
                        padx=10, pady=8)
        card.pack(fill="x", padx=4, pady=4)
        card.columnconfigure(0, weight=1)

        meta = f"第 {i + 1} 段" + (f"　·　{item.note}" if item.note else "")
        meta_lbl = tk.Label(card, text=meta, bg=bg, fg=GREY, font=self.f_small, anchor="w", justify="left")
        meta_lbl.grid(row=0, column=0, sticky="ew")
        auto_wrap(meta_lbl, 24)
        if i > 0 and item.confidence == "low":
            prev = self.items[i - 1].text
            ctx = tk.Label(card, text="上一段：" + (prev[:40] + "…" if len(prev) > 40 else prev),
                           bg=bg, fg=GREY, font=self.f_small, anchor="w", justify="left")
            ctx.grid(row=1, column=0, sticky="ew")
            auto_wrap(ctx, 24)
        text = item.text if len(item.text) <= 120 else item.text[:120] + "…"
        body = tk.Label(card, text=text, bg=bg, font=self.f_bold, anchor="w", justify="left")
        body.grid(row=2, column=0, sticky="ew", pady=(4, 6))
        auto_wrap(body, 24)

        row = tk.Frame(card, bg=bg)
        row.grid(row=3, column=0, sticky="w")
        tk.Label(row, text="这一段是：", bg=bg).pack(side="left")
        box = ttk.Combobox(row, values=list(LABEL_TO_ROLE), state="readonly",
                           width=8, font=self.f_base)
        box.set(item.label)
        box.pack(side="left", padx=(2, 8))
        ok = ttk.Button(row, text="✓ 对的", command=lambda: confirm(box.get()))
        if item.confidence == "low":
            ok.pack(side="left")

        def paint(color):
            for w in [card, row] + [c for c in card.winfo_children() + row.winfo_children()
                                    if isinstance(c, tk.Label)]:
                w.configure(bg=color)

        def confirm(label):
            role = LABEL_TO_ROLE[label]
            item.role = role
            item.lead_len = lead_len(role, item.text)
            item.confirmed = True
            if item.confidence == "low":
                paint(GREEN)
                ok.configure(text="✓ 已确认")
            self._refresh_summary()

        box.bind("<<ComboboxSelected>>", lambda e: confirm(box.get()))

    # ---------- 生成 ----------

    def _generate(self):
        pending = self._pending()
        if pending and not messagebox.askyesno(
                APP_NAME, f"还有 {pending} 段没有确认，这些段落会按程序的猜测排版。\n\n仍然生成吗？"):
            return
        src = Path(self.src_path)
        out = filedialog.asksaveasfilename(
            defaultextension=".docx", initialdir=src.parent,
            initialfile=src.stem + "_已排版.docx", filetypes=[("Word 文档", "*.docx")])
        if not out:
            return
        if Path(out).resolve() == src.resolve():
            messagebox.showerror(APP_NAME, "不能覆盖原稿，请换一个文件名。")
            return
        try:
            render(self.items, parse(self._req()), out)
        except PermissionError:
            messagebox.showerror(APP_NAME, "保存失败：目标文件可能正在 Word 里打开，请先关闭它。")
            return
        self.status.configure(text=f"已生成：{out}")
        if messagebox.askyesno(APP_NAME, "排版完成！现在打开文件看看吗？") and sys.platform == "win32":
            os.startfile(out)


def main():
    if sys.platform == "win32":
        try:  # 高分屏下文字不发虚；字号会按系统缩放比例自动放大
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
