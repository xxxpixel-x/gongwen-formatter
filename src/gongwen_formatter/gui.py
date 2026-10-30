"""图形界面（tkinter，Python 自带，打包后体积小）。

左边卡片：① 格式要求 → 下方表格实时显示“程序理解成了什么”
右边卡片：② 原稿预览 → 排好版的预览；程序不确定的段落标黄，点一下就能改类型
底部：③ 生成排版文件；生成后可以直接打开文件或所在文件夹

布局全部用 grid + 权重，窗口缩放、高分屏下都不会遮挡文字。
快捷键：Ctrl+O 选择原稿，Ctrl+Enter 确认并跳到下一处，Ctrl+S 生成。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .classify import ROLE_LABELS, Role, lead_len
from .extract import extract
from .fonts import installed_fonts, missing_fonts
from .preview import Preview
from .render import read_document, render
from .spec import DEFAULT_TEXT, DISPLAY_ROLES, PARAGRAPH_ROLES, describe_role, fonts_used, parse

APP_NAME = "公文格式整理器"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "gongwen-formatter"
REQ_FILE = CONFIG_DIR / "requirements.txt"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

SPEC_COLUMNS = ["字体", "字号", "加粗", "对齐", "缩进", "行距"]
LABEL_TO_ROLE = {ROLE_LABELS[r]: r for r in PARAGRAPH_ROLES}

# 配色：浅灰底 + 白色卡片，蓝色只用在主要操作上
BG, CARD, BORDER = "#f3f4f6", "#ffffff", "#dfe3e8"
TEXT, MUTED = "#1f2328", "#6b7280"
ACCENT, ACCENT_DARK, ACCENT_OFF = "#2563eb", "#1d4ed8", "#a9bde8"
WARN_BG, WARN_FG = "#fff6db", "#8a5a00"
OK_BG, OK_FG = "#e7f5ea", "#1e6b34"
ERR_FG = "#c0392b"
ZEBRA = "#f7f8fa"

HINT_SHORT = "每行写一类段落：先写类型，再写格式。例：一级标题（黑体，三号，顶格）"
HINT_FULL = (
    "也可以点“从模板读取…”，选一份已经排好版的公文，程序会自动写出它的格式要求；"
    "或者点“导入txt…”读入以前保存的要求。\n\n"
    "每行写一类段落，先写类型，再写格式。\n\n"
    "类型：标题、副标题、一级标题、二级标题、三级标题、四级标题、五级标题、正文、附件、附件名称、落款、日期、表格标题、表头、表格\n"
    "（编号依次为 一、→（一）→1.→（1）→1）；四、五级标题没写时与正文相同；"
    "表格默认保持原样，写了表头（表格第一行）、表格（其余单元格）时只改写到的项）\n\n"
    "格式：字体（仿宋_GB2312、黑体……）、字号（三号、小四、16磅）、加粗、"
    "对齐（居中、两端对齐、右对齐）、缩进（首行缩进2字符、顶格、右缩进1字符）、"
    "行距（行距固定值28磅、1.5倍行距）、段前段后（段前0.5行）\n\n"
    "另外还可以写：\n英文、数字：Times New Roman\n页边距：上37毫米，下35毫米，左28毫米，右26毫米\n"
    "页眉1.5厘米，页脚2.8厘米\n\n"
    "没写到的内容按默认要求（规范图片）处理。"
)
SHORTCUTS = "Ctrl+O 选择原稿　·　Ctrl+Enter 确认并跳到下一处　·　Ctrl+S 生成"


def auto_wrap(label: tk.Widget, padding: int = 8):
    """让 Label 的换行宽度跟着自身宽度走，窗口变窄时文字自动换行而不是被截断。"""
    label.bind("<Configure>", lambda e: label.configure(wraplength=max(e.width - padding, 50)))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self.configure(background=BG)
        self._set_icon()
        self.src_path: str | None = None
        self.out_path: str | None = None
        self.items = []
        self.read_warnings: list[str] = []
        self.fonts: set[str] | None = None
        self._fonts_ready = False
        self._parse_job = None
        self.sel: int | None = None   # 预览里当前选中的段落
        self.settings = self._load_settings()

        self._init_style()
        self._build()
        self._bind_keys()
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
        self.f_title = tkfont.Font(family=family, size=13, weight="bold")
        self.f_go = tkfont.Font(family=family, size=12, weight="bold")
        self.f_edit = tkfont.Font(family=family, size=11)
        self.f_link = tkfont.Font(family=family, size=9, underline=True)
        # 只给 tk 控件设默认字体；ttk 控件的字体走样式，否则“*Font”会盖掉各个样式里的字号
        for cls in ("Text", "Label", "Button", "Listbox", "Entry"):
            self.option_add(f"*{cls}.Font", self.f_base)
        self.option_add("*TCombobox*Listbox.font", self.f_base)

        style.configure(".", font=self.f_base)
        style.configure("TFrame", background=CARD)
        style.configure("TLabel", background=CARD, foreground=TEXT)
        style.configure("TButton", background=CARD)
        style.configure("TPanedwindow", background=CARD)
        style.configure("Bg.TPanedwindow", background=BG)
        style.configure("Title.TLabel", font=self.f_title)
        style.configure("Muted.TLabel", foreground=MUTED, font=self.f_small)
        style.configure("Sub.TLabel", font=self.f_bold)
        style.configure("Warn.TLabel", foreground=ERR_FG)
        style.configure("Info.TLabel", foreground=WARN_FG)
        # 行高按字体实际高度算：高分屏（150%、200% 缩放）下也不会挤压文字
        line = self.f_base.metrics("linespace")
        style.configure("Treeview", rowheight=line + 8, font=self.f_base, background=CARD,
                        fieldbackground=CARD, borderwidth=0)
        style.configure("Treeview.Heading", font=self.f_bold)

    def _size_window(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(sw * 0.8), int(sh * 0.8)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
        self.minsize(int(sw * 0.5), int(sh * 0.5))
        if sys.platform == "win32":
            self.state("zoomed")

    def _card(self, master) -> tk.Frame:
        return tk.Frame(master, background=CARD, highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=BORDER)

    def _header(self, card, number: str, title: str) -> ttk.Frame:
        """卡片标题行：蓝色序号 + 标题；右边留给按钮（从第 2 列开始放）。"""
        head = ttk.Frame(card)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        head.columnconfigure(1, weight=1)
        title_box = ttk.Frame(head)
        title_box.grid(row=0, column=0, sticky="w")
        tk.Label(title_box, text=number, background=ACCENT, foreground="white", font=self.f_bold,
                 width=2).pack(side="left")
        ttk.Label(title_box, text=title, style="Title.TLabel").pack(side="left", padx=(8, 0))
        return head

    def _link(self, master, text: str, command) -> tk.Label:
        lbl = tk.Label(master, text=text, background=CARD, foreground=ACCENT, font=self.f_link, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: command())
        return lbl

    # ---------- 布局 ----------

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(self, orient="horizontal", style="Bg.TPanedwindow")
        panes.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))
        left = self._card(panes)
        right = self._card(panes)
        panes.add(left, weight=4)
        panes.add(right, weight=5)
        # 初始左边略窄：预览需要更多宽度
        place_sash = lambda: panes.sashpos(0, int(panes.winfo_width() * 0.44))  # noqa: E731
        self.after(50, place_sash)
        self.bind("<Map>", lambda e: e.widget is self and self.after(50, place_sash), add="+")
        self._build_left(left)
        self._build_right(right)
        self._build_bottom()

    def _build_left(self, card):
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        head = self._header(card, "1", "格式要求")
        ttk.Button(head, text="从模板读取…", command=self._read_template).grid(row=0, column=2)
        ttk.Button(head, text="导入txt…", command=self._import_requirements).grid(row=0, column=3, padx=(6, 0))
        hint = ttk.Label(card, text=HINT_SHORT, style="Muted.TLabel")
        hint.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        auto_wrap(hint)

        # 上：输入框；下：程序理解的格式。中间的分隔条可以拖动
        split = ttk.PanedWindow(card, orient="vertical")
        split.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        top, bottom = ttk.Frame(split), ttk.Frame(split)
        split.add(top, weight=1)
        split.add(bottom, weight=1)
        self.after(60, lambda: split.sashpos(0, int(split.winfo_height() * 0.46)))

        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        box = tk.Frame(top, background=CARD, highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=ACCENT)
        box.grid(row=0, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.req_text = tk.Text(box, height=6, wrap="word", undo=True, relief="flat", borderwidth=0,
                                padx=10, pady=8, font=self.f_edit, highlightthickness=0,
                                spacing1=2, spacing3=2, foreground=TEXT)
        req_sb = ttk.Scrollbar(box, orient="vertical", command=self.req_text.yview)
        self.req_text.configure(yscrollcommand=req_sb.set)
        self.req_text.grid(row=0, column=0, sticky="nsew")
        req_sb.grid(row=0, column=1, sticky="ns")
        self.req_text.bind("<<Modified>>", self._on_req_change)
        # 输入框获得焦点时边框变蓝
        self.req_text.bind("<FocusIn>", lambda e: box.configure(highlightbackground=ACCENT), add="+")
        self.req_text.bind("<FocusOut>", lambda e: box.configure(highlightbackground=BORDER), add="+")

        btns = ttk.Frame(top)
        btns.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        btns.columnconfigure(2, weight=1)
        ttk.Button(btns, text="恢复默认要求", command=self._reset_requirements).grid(row=0, column=0)
        ttk.Button(btns, text="另存txt…", command=self._export_requirements).grid(row=0, column=1, padx=6)
        self._link(btns, "写法说明", lambda: messagebox.showinfo("格式要求的写法", HINT_FULL)).grid(
            row=0, column=3, sticky="e")

        self.spec_msg = ttk.Label(top, style="Warn.TLabel")
        self.spec_msg.grid(row=2, column=0, sticky="ew", pady=(6, 4))
        auto_wrap(self.spec_msg)

        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)
        ttk.Label(bottom, text="程序理解的格式（核对一下）", style="Sub.TLabel").grid(
            row=0, column=0, sticky="w", pady=(10, 6))
        tree_box = tk.Frame(bottom, background=CARD, highlightthickness=1, highlightbackground=BORDER)
        tree_box.grid(row=1, column=0, sticky="nsew")
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        self.spec_tree = ttk.Treeview(tree_box, columns=SPEC_COLUMNS, height=5, selectmode="none")
        self.spec_tree.heading("#0", text="段落类型")
        em = self.f_base.measure("中")
        self.spec_tree.column("#0", width=int(em * 5.5), minwidth=int(em * 5), stretch=False)
        for col, n in zip(SPEC_COLUMNS, (7.5, 3, 2.5, 4.5, 8, 6)):
            self.spec_tree.heading(col, text=col)
            self.spec_tree.column(col, width=int(em * n), minwidth=int(em * n), stretch=True)  # 窄时横向滚动，不截断
        self.spec_tree.tag_configure("odd", background=ZEBRA)
        self.spec_tree.tag_configure("page", foreground=MUTED)
        tsb = ttk.Scrollbar(tree_box, orient="vertical", command=self.spec_tree.yview)
        hsb = ttk.Scrollbar(tree_box, orient="horizontal", command=self.spec_tree.xview)
        self.spec_tree.configure(yscrollcommand=tsb.set, xscrollcommand=hsb.set)
        self.spec_tree.grid(row=0, column=0, sticky="nsew")
        tsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

    def _build_right(self, card):
        card.columnconfigure(0, weight=1)
        card.rowconfigure(4, weight=1)

        head = self._header(card, "2", "原稿预览")
        self.file_label = ttk.Label(head, text="", style="Muted.TLabel")
        self.file_label.grid(row=0, column=1, sticky="w", padx=(12, 8))
        self.open_btn = ttk.Button(head, text="选择 Word 文件…", command=self._choose_file)
        self.open_btn.grid(row=0, column=2)

        # 识别结果的汇总（黄色：还有待确认；绿色：全部完成）
        self.banner = tk.Frame(card, background=WARN_BG)
        self.banner.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 0))
        self.banner.columnconfigure(0, weight=1)
        self.banner_text = tk.Label(self.banner, background=WARN_BG, foreground=WARN_FG, font=self.f_bold,
                                    anchor="w", justify="left", padx=12, pady=8)
        self.banner_text.grid(row=0, column=0, sticky="ew")
        auto_wrap(self.banner_text, 24)
        self.banner.grid_remove()

        self.warn_label = ttk.Label(card, style="Info.TLabel")
        self.warn_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 0))
        auto_wrap(self.warn_label)
        self.warn_label.grid_remove()

        # 当前选中的段落：说明单独占一行，窗口窄时也不会被挤成一条竖线
        bar = ttk.Frame(card)
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 8))
        bar.columnconfigure(0, weight=1)
        self.sel_label = ttk.Label(bar, style="Sub.TLabel")
        self.sel_label.grid(row=0, column=0, sticky="w")
        ttk.Label(bar, text="这一段是：").grid(row=0, column=1, padx=(8, 2))
        self.role_box = ttk.Combobox(bar, values=list(LABEL_TO_ROLE), state="disabled", width=8, font=self.f_base)
        self.role_box.grid(row=0, column=2)
        self.role_box.bind("<<ComboboxSelected>>", lambda e: self._set_role(LABEL_TO_ROLE[self.role_box.get()]))
        self.ok_btn = ttk.Button(bar, text="✓ 对的", command=self._confirm, state="disabled")
        self.ok_btn.grid(row=0, column=3, padx=6)
        self.next_btn = ttk.Button(bar, text="下一处待确认 ▸", command=self._next_pending, state="disabled")
        self.next_btn.grid(row=0, column=4)
        self.sel_note = ttk.Label(bar, style="Muted.TLabel")
        self.sel_note.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(2, 0))
        auto_wrap(self.sel_note)
        self.sel_bar = bar
        bar.grid_remove()   # 选了原稿才显示

        self.preview = Preview(card, on_click=self._select)
        self.preview.grid(row=4, column=0, sticky="nsew", padx=16)
        self._build_placeholder(self.preview.placeholder)

        note = ttk.Label(card, style="Muted.TLabel",
                         text="预览仅供核对：不分页，两端对齐显示为左对齐，没装的字体用相近字体代替，以生成的 Word 文件为准。")
        note.grid(row=5, column=0, sticky="ew", padx=16, pady=(6, 14))
        auto_wrap(note)

    def _build_placeholder(self, ph: tk.Frame):
        bg = ph.cget("background")
        tk.Label(ph, text="📄", background=bg, font=(self.f_base.cget("family"), 36)).pack()
        tk.Label(ph, text="还没有选择原稿", background=bg, foreground=TEXT, font=self.f_title).pack(pady=(6, 2))
        tk.Label(ph, text="选择一份格式混乱的 Word 文件（.docx），这里会显示按左边要求排好版的效果。\n"
                          "程序拿不准的段落会标黄，点一下就能改。原稿不会被修改。",
                 background=bg, foreground=MUTED, font=self.f_base, justify="center").pack(pady=(0, 14))
        self._primary(ph, "选择 Word 文件…", self._choose_file).pack()
        tk.Label(ph, text="快捷键 Ctrl+O", background=bg, foreground=MUTED, font=self.f_small).pack(pady=(6, 0))

    def _primary(self, master, text: str, command) -> tk.Button:
        """主要操作按钮：蓝底白字。"""
        return tk.Button(master, text=text, command=command, font=self.f_go, foreground="white",
                         background=ACCENT, activebackground=ACCENT_DARK, activeforeground="white",
                         disabledforeground="#eef2fb", relief="flat", borderwidth=0, padx=20, pady=7,
                         cursor="hand2")

    def _build_bottom(self):
        bottom = tk.Frame(self, background=CARD, highlightthickness=1, highlightbackground=BORDER)
        bottom.grid(row=1, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        left = ttk.Frame(bottom)
        left.grid(row=0, column=0, sticky="ew", padx=16, pady=10)
        left.columnconfigure(0, weight=1)
        self.status = ttk.Label(left, text=SHORTCUTS, style="Muted.TLabel")
        self.status.grid(row=0, column=0, sticky="ew")
        auto_wrap(self.status)
        # 生成成功后出现：打开文件 / 打开所在文件夹
        self.done_btns = ttk.Frame(left)
        self.done_btns.grid(row=0, column=1, sticky="e")
        ttk.Button(self.done_btns, text="打开文件", command=self._open_output).pack(side="left", padx=(8, 0))
        ttk.Button(self.done_btns, text="打开所在文件夹", command=self._open_output_folder).pack(side="left", padx=6)
        self.done_btns.grid_remove()

        go_box = tk.Frame(bottom, background=CARD)
        go_box.grid(row=0, column=1, sticky="e", padx=16, pady=10)
        tk.Label(go_box, text="3", background=ACCENT, foreground="white", font=self.f_bold, width=2).pack(
            side="left", padx=(0, 8))
        self.go_btn = self._primary(go_box, "生成排版文件", self._generate)
        self.go_btn.pack(side="left")
        self._set_go(False)

    def _set_go(self, enabled: bool):
        self.go_btn.configure(state="normal" if enabled else "disabled",
                              background=ACCENT if enabled else ACCENT_OFF,
                              cursor="hand2" if enabled else "arrow")

    def _bind_keys(self):
        self.bind("<Control-o>", lambda e: self._choose_file())
        self.bind("<Control-s>", lambda e: self._generate() if self.items else None)
        self.bind("<Control-Return>", lambda e: self._confirm() if self.sel is not None else None)

    # ---------- 设置（记住上次的文件夹） ----------

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember_dir(self, path: str):
        self.settings["last_dir"] = str(Path(path).parent)
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.settings, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _last_dir(self) -> str | None:
        d = self.settings.get("last_dir")
        return d if d and Path(d).is_dir() else None

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
        rows = []
        for role in DISPLAY_ROLES:
            d = describe_role(self.spec, role)
            rows.append((ROLE_LABELS[role], [d[c] for c in SPEC_COLUMNS], ()))
        rows.append(("英文数字", [self.spec.latin_font, "", "", "", "", ""], ("page",)))
        t, b, l, r = self.spec.margins_mm
        rows.append(("页边距", [f"上{t:g} 下{b:g}", "", "", "", f"左{l:g} 右{r:g}（毫米）", ""], ("page",)))
        h, f = self.spec.header_mm, self.spec.footer_mm
        rows.append(("页眉页脚", [f"页眉{h:g}" if h is not None else "页眉默认", "", "", "",
                                 f"页脚{f:g}（毫米）" if f is not None else "页脚默认", ""], ("page",)))
        for n, (label, values, tags) in enumerate(rows):
            self.spec_tree.insert("", "end", text=label, values=values, tags=tags + (("odd",) if n % 2 else ()))
        self._fit_columns([values for _, values, tags in rows if not tags])
        self._update_spec_msg()
        self._save_requirements()
        self._refresh_preview()

    def _fit_columns(self, rows: list[list[str]]):
        """列宽按内容算，长字体名不会被截断；窗口窄时出现横向滚动条。"""
        pad = self.f_base.measure("中") * 2
        for i, col in enumerate(SPEC_COLUMNS):
            w = max([self.f_bold.measure(col)] + [self.f_base.measure(r[i]) for r in rows]) + pad
            self.spec_tree.column(col, width=w, minwidth=w)

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
        if msgs:
            self.spec_msg.grid()
        else:
            self.spec_msg.grid_remove()

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
        path = filedialog.askopenfilename(title="选择一份已经排好版的公文（模板）", initialdir=self._last_dir(),
                                          filetypes=[("Word 文档", "*.docx")])
        if not path:
            return
        self._remember_dir(path)
        try:
            result = extract(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"读取模板失败：{e}")
            return
        if not self._replace_requirements(result.text, "模板的格式"):
            return
        messagebox.showinfo(APP_NAME, f"已从模板读取 {len(result.found)} 类段落的格式，填进了“格式要求”。\n\n"
                            "以 # 开头的行是说明，不影响排版。请对照下方“程序理解的格式”核对一遍，"
                            "需要的话直接在输入框里改。")

    def _replace_requirements(self, text: str, what: str) -> bool:
        """用 text 替换输入框；输入框里有自己改过的内容时先问一下。"""
        current = self._req().strip()
        if current and current not in (DEFAULT_TEXT.strip(), text.strip()) and not messagebox.askyesno(
                APP_NAME, f"用{what}替换输入框里现在的要求吗？\n（想保留现在的要求，可以先点“另存txt…”）"):
            return False
        self.req_text.delete("1.0", "end")
        self.req_text.insert("1.0", text)
        return True

    def _import_requirements(self):
        path = filedialog.askopenfilename(title="选择格式要求文本文件", initialdir=self._last_dir(),
                                          filetypes=[("文本文件", "*.txt")])
        if not path:
            return
        self._remember_dir(path)
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "gb18030"):   # 记事本存的 UTF-8 或 ANSI（GBK）都能读
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            messagebox.showerror(APP_NAME, "读不出这个文件的文字，请在记事本里另存为 UTF-8 编码后再导入。")
            return
        self._replace_requirements(text.replace("\r\n", "\n"), f"《{Path(path).name}》里的要求")

    def _reset_requirements(self):
        if messagebox.askyesno(APP_NAME, "恢复成默认格式要求（规范图片）？当前内容会被覆盖。"):
            self.req_text.delete("1.0", "end")
            self.req_text.insert("1.0", DEFAULT_TEXT)

    def _export_requirements(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="格式要求.txt",
                                            initialdir=self._last_dir(), filetypes=[("文本文件", "*.txt")])
        if path:
            self._remember_dir(path)
            Path(path).write_text(self._req(), encoding="utf-8")
            self._show_status(f"格式要求已保存到：{path}")

    # ---------- 原稿：排版预览，拿不准的段落标黄 ----------

    def _choose_file(self):
        path = filedialog.askopenfilename(title="选择需要排版的原稿", initialdir=self._last_dir(),
                                          filetypes=[("Word 文档", "*.docx")])
        if not path:
            return
        self._remember_dir(path)
        try:
            result = read_document(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"读取失败：{e}\n\n如果是 .doc 或 .wps 文件，请先在 Word/WPS 里另存为 .docx。")
            return
        self.src_path, self.items, self.read_warnings = path, result.items, result.warnings
        for it in self.items:
            it.confirmed = it.confidence != "low"
        name = Path(path).name
        self.file_label.configure(text=name)
        self.open_btn.configure(text="换一个文件…")
        self.title(f"{name} - {APP_NAME}  v{__version__}")
        self._set_go(bool(self.items))
        self._show_status(SHORTCUTS)
        self.sel_bar.grid()
        self.sel = None
        self.preview.show(self.items, self.spec, None, keep_scroll=False)
        pending = self._pending_idxs()
        if pending:
            self._select(pending[0], scroll=True)
        else:
            self._update_bar()
        self._refresh_summary()

    def _pending_idxs(self) -> list[int]:
        return [i for i, it in enumerate(self.items) if not getattr(it, "confirmed", True)]

    def _pending(self) -> int:
        return len(self._pending_idxs())

    def _refresh_summary(self):
        if not self.items:
            return
        total, pending = len(self.items), self._pending()
        low = sum(it.confidence == "low" for it in self.items)
        if pending:
            bg, fg = WARN_BG, WARN_FG
            text = (f"还有 {pending} 段需要确认（已确认 {low - pending}/{low}，共 {total} 段）"
                    "　黄色段落是程序拿不准的，看看识别得对不对")
        else:
            bg, fg = OK_BG, OK_FG
            text = f"✓ 共 {total} 段，全部识别完成，可以生成了。发现排错的段落，点它就能改类型。"
        self.banner.configure(background=bg)
        self.banner_text.configure(text=text, background=bg, foreground=fg)
        self.banner.grid()
        if self.read_warnings:
            self.warn_label.configure(text="\n".join("· " + w for w in self.read_warnings))
            self.warn_label.grid()
        else:
            self.warn_label.grid_remove()

    def _refresh_preview(self):
        if self.items:
            self.preview.show(self.items, self.spec, self.sel)

    def _select(self, i: int, scroll: bool = False):
        self.sel = i
        self._update_bar()
        self._refresh_preview()
        if scroll:
            self.preview.see(i)

    def _update_bar(self):
        self.next_btn.configure(state="normal" if self._pending() else "disabled")
        if self.sel is None:
            self.sel_label.configure(text="点预览里的任意一段，可以查看和修改它的类型")
            self.sel_note.configure(text="")
            self.role_box.set("")
            self.role_box.configure(state="disabled")
            self.ok_btn.configure(state="disabled")
            return
        it = self.items[self.sel]
        status = "待确认" if not it.confirmed else ("已确认" if it.confidence == "low" else "")
        self.sel_label.configure(text=f"第 {self.sel + 1} 段" + (f" · {status}" if status else ""))
        self.sel_note.configure(text=it.note)
        self.role_box.configure(state="readonly")
        self.role_box.set(it.label)
        self.ok_btn.configure(state="disabled" if it.confirmed else "normal")

    def _set_role(self, role: Role):
        it = self.items[self.sel]
        it.role, it.lead_len, it.confirmed = role, lead_len(role, it.text), True
        self._after_change()

    def _confirm(self):
        if self.sel is None:
            return
        self.items[self.sel].confirmed = True
        self._after_change(advance=True)

    def _after_change(self, advance: bool = False):
        self._refresh_summary()
        if advance and self._pending():
            self._next_pending()   # 确认后自动跳到下一处
        else:
            self._update_bar()
            self._refresh_preview()

    def _next_pending(self):
        idxs = self._pending_idxs()
        if idxs:
            start = -1 if self.sel is None else self.sel
            self._select(next((i for i in idxs if i > start), idxs[0]), scroll=True)

    # ---------- 生成 ----------

    def _generate(self):
        if not self.items:
            return
        pending = self._pending()
        if pending and not messagebox.askyesno(
                APP_NAME, f"还有 {pending} 段没有确认，这些段落会按程序的猜测排版。\n\n"
                          "仍然生成吗？（选“否”会跳到第一处待确认的段落）"):
            self._next_pending()
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
            render(self.items, parse(self._req()), out, source_path=src)
        except PermissionError:
            messagebox.showerror(APP_NAME, "保存失败：目标文件可能正在 Word 里打开，请先关闭它。")
            return
        except (OSError, ValueError) as e:
            messagebox.showerror(APP_NAME, f"保存失败：{e}")
            return
        self.out_path = out
        self._show_status(f"✓ 已生成《{Path(out).name}》", done=True)

    def _show_status(self, text: str, done: bool = False):
        self.status.configure(text=text, style="Sub.TLabel" if done else "Muted.TLabel",
                              foreground=OK_FG if done else MUTED)
        if done:
            self.done_btns.grid()
        else:
            self.done_btns.grid_remove()

    def _open_output(self):
        if self.out_path and sys.platform == "win32":
            os.startfile(self.out_path)

    def _open_output_folder(self):
        if self.out_path and sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.out_path)])


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
