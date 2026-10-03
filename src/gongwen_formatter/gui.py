"""图形界面（tkinter，Python 自带，打包后体积小）。

左边：① 格式要求 → 下方表格实时显示“程序理解成了什么”
右边：② 选择原稿 → 排好版的预览；程序不确定的段落标黄，点一下就能改类型
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
from .classify import ROLE_LABELS, Role, lead_len
from .extract import extract
from .fonts import installed_fonts, missing_fonts
from .preview import Preview
from .render import read_document, render
from .spec import DEFAULT_TEXT, DISPLAY_ROLES, describe, fonts_used, parse

APP_NAME = "公文格式整理器"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "gongwen-formatter"
REQ_FILE = CONFIG_DIR / "requirements.txt"

SPEC_COLUMNS = ["字体", "字号", "加粗", "对齐", "缩进", "行距"]
LABEL_TO_ROLE = {ROLE_LABELS[r]: r for r in DISPLAY_ROLES}

GREY = "#6b6b6b"

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
        self.sel: int | None = None   # 预览里当前选中的段落

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
        ttk.Button(btns, text="导入txt…", command=self._import_requirements).pack(side="left", padx=6)
        ttk.Button(btns, text="另存txt…", command=self._export_requirements).pack(side="left")

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

        self.doc_msg = ttk.Label(right, text="选择文件后，这里显示排好版的预览。黄色是程序拿不准的段落，点一下就能改类型。",
                                 style="Hint.TLabel")
        self.doc_msg.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        auto_wrap(self.doc_msg)

        # 当前选中的段落：改类型、确认、跳到下一处
        bar = ttk.Frame(right)
        bar.grid(row=2, column=0, sticky="ew", pady=(2, 6))
        bar.columnconfigure(0, weight=1)
        self.sel_label = ttk.Label(bar, font=self.f_bold)
        self.sel_label.grid(row=0, column=0, sticky="ew")
        auto_wrap(self.sel_label)
        ttk.Label(bar, text="这一段是：").grid(row=0, column=1, padx=(8, 2))
        self.role_box = ttk.Combobox(bar, values=list(LABEL_TO_ROLE), state="disabled", width=8, font=self.f_base)
        self.role_box.grid(row=0, column=2)
        self.role_box.bind("<<ComboboxSelected>>", lambda e: self._set_role(LABEL_TO_ROLE[self.role_box.get()]))
        self.ok_btn = ttk.Button(bar, text="✓ 对的", command=self._confirm, state="disabled")
        self.ok_btn.grid(row=0, column=3, padx=6)
        self.next_btn = ttk.Button(bar, text="下一处待确认 ▸", command=self._next_pending, state="disabled")
        self.next_btn.grid(row=0, column=4)

        self.preview = Preview(right, on_click=self._select)
        self.preview.grid(row=3, column=0, sticky="nsew")
        note = ttk.Label(right, style="Hint.TLabel",
                         text="预览仅供核对：不分页，两端对齐显示为左对齐，没装的字体用相近字体代替，以生成的 Word 文件为准。")
        note.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        auto_wrap(note)

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
        self._refresh_preview()

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
        path = filedialog.askopenfilename(title="选择格式要求文本文件", filetypes=[("文本文件", "*.txt")])
        if not path:
            return
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
                                            filetypes=[("文本文件", "*.txt")])
        if path:
            Path(path).write_text(self._req(), encoding="utf-8")

    # ---------- 原稿：排版预览，拿不准的段落标黄 ----------

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
        total, pending = len(self.items), self._pending()
        if not self.items:
            return
        if pending:
            text = (f"共 {total} 段，有 {pending} 段程序拿不准（预览中的黄色段落）。"
                    "识别对了就点“✓ 对的”，不对就在“这一段是”里选正确的类型。")
        else:
            text = f"共 {total} 段，全部识别完成，可以直接生成。发现排错的段落，点它就能改类型。"
        self.doc_msg.configure(text="\n".join([text] + self.read_warnings))
        self.status.configure(text="")

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
            self.sel_label.configure(text="点预览里的任意一段，可以查看和修改它的类型。" if self.items else "")
            self.role_box.set("")
            self.role_box.configure(state="disabled")
            self.ok_btn.configure(state="disabled")
            return
        it = self.items[self.sel]
        status = "待确认" if not it.confirmed else ("已确认" if it.confidence == "low" else "")
        self.sel_label.configure(text=f"第 {self.sel + 1} 段" + (f"（{status}）" if status else "")
                                 + (f"　·　{it.note}" if it.note else ""))
        self.role_box.configure(state="readonly")
        self.role_box.set(it.label)
        self.ok_btn.configure(state="disabled" if it.confirmed else "normal")

    def _set_role(self, role: Role):
        it = self.items[self.sel]
        it.role, it.lead_len, it.confirmed = role, lead_len(role, it.text), True
        self._after_change()

    def _confirm(self):
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
            render(self.items, parse(self._req()), out, source_path=src)
        except PermissionError:
            messagebox.showerror(APP_NAME, "保存失败：目标文件可能正在 Word 里打开，请先关闭它。")
            return
        except (OSError, ValueError) as e:
            messagebox.showerror(APP_NAME, f"保存失败：{e}")
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
