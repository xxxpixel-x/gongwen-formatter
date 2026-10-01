"""图形界面（tkinter，Python 自带，打包后体积小）。

左边：① 输入格式要求 → 下方表格实时显示“程序理解成了什么”
右边：② 选择原稿 → 逐段显示识别结果，双击“类型”可以改
底部：③ 生成排版文件
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .classify import ROLE_LABELS, Role, h2_lead_len, h3_lead_len
from .render import read_document, render
from .spec import DEFAULT_TEXT, DISPLAY_ROLES, describe, fonts_used, parse

APP_NAME = "公文格式整理器"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "gongwen-formatter"
REQ_FILE = CONFIG_DIR / "requirements.txt"

SPEC_COLUMNS = ["字体", "字号", "加粗", "对齐", "缩进", "行距"]
LABEL_TO_ROLE = {ROLE_LABELS[r]: r for r in DISPLAY_ROLES}

HINT = ("每行写一类段落：先写类型（标题、副标题、一级标题、二级标题、三级标题、正文、"
        "附件、附件名称、落款、日期），再写格式，例如：\n"
        "一级标题（黑体，三号，顶格）    正文：仿宋_GB2312 三号 首行缩进2字符 行距固定值28磅\n"
        "另可写：英文、数字：Times New Roman    页边距：上37毫米，下35毫米，左28毫米，右26毫米")


def installed_fonts() -> set[str] | None:
    """读取 Windows 已安装字体名；其他系统返回 None（不检查）。"""
    if sys.platform != "win32":
        return None
    import winreg
    names: set[str] = set()
    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key_path) as key:
                i = 0
                while True:
                    try:
                        name = winreg.EnumValue(key, i)[0]
                    except OSError:
                        break
                    i += 1
                    name = name.split(" (")[0]
                    names.update(part.strip() for part in name.split("&"))
        except OSError:
            continue
    return names


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry("1280x720")
        self.minsize(1000, 560)
        if sys.platform == "win32":
            self.state("zoomed")  # 默认最大化
        self.src_path: str | None = None
        self.items = []
        self.read_warnings: list[str] = []
        self.fonts = installed_fonts()
        self._parse_job = None

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        base_font = ("Microsoft YaHei UI", 10) if sys.platform == "win32" else ("Noto Sans CJK SC", 10)
        self.option_add("*Font", base_font)
        style.configure("Treeview", rowheight=26, font=base_font)
        style.configure("Treeview.Heading", font=(base_font[0], 10, "bold"))
        style.configure("Step.TLabel", font=(base_font[0], 12, "bold"))
        style.configure("Go.TButton", font=(base_font[0], 12, "bold"), padding=8)

        self._build()
        self.req_text.insert("1.0", self._load_requirements())
        self._reparse()

    # ---------- 布局 ----------

    def _build(self):
        # 底部
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(side="bottom", fill="x")
        self.go_btn = ttk.Button(bottom, text="③ 生成排版文件", style="Go.TButton",
                                 command=self._generate, state="disabled")
        self.go_btn.pack(side="right")
        self.status = ttk.Label(bottom, foreground="#666")
        self.status.pack(side="left")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        # 左：格式要求
        left = ttk.Frame(panes, padding=4)
        panes.add(left, weight=1)
        ttk.Label(left, text="① 格式要求", style="Step.TLabel").pack(anchor="w")
        ttk.Label(left, text=HINT, foreground="#666", wraplength=560, justify="left").pack(anchor="w", pady=(2, 6))
        self.req_text = tk.Text(left, height=9, wrap="word", undo=True, relief="solid", borderwidth=1)
        self.req_text.pack(fill="x")
        self.req_text.bind("<<Modified>>", self._on_req_change)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="恢复默认要求", command=self._reset_requirements).pack(side="left")
        ttk.Button(btns, text="从文本文件导入…", command=self._import_requirements).pack(side="left", padx=6)
        ttk.Button(btns, text="另存要求…", command=self._export_requirements).pack(side="left")

        self.spec_msg = ttk.Label(left, foreground="#c0392b", wraplength=560, justify="left")
        self.spec_msg.pack(anchor="w")
        ttk.Label(left, text="程序理解的格式（核对一下）：").pack(anchor="w", pady=(6, 2))
        spec_frame = ttk.Frame(left)
        spec_frame.pack(fill="both", expand=True)
        self.spec_tree = ttk.Treeview(spec_frame, columns=SPEC_COLUMNS, height=6)
        self.spec_tree.heading("#0", text="段落类型")
        self.spec_tree.column("#0", width=80, stretch=False)
        for col, w in zip(SPEC_COLUMNS, (120, 50, 40, 70, 150, 100)):
            self.spec_tree.heading(col, text=col)
            self.spec_tree.column(col, width=w, stretch=col == "缩进")
        spec_sb = ttk.Scrollbar(spec_frame, orient="vertical", command=self.spec_tree.yview)
        self.spec_tree.configure(yscrollcommand=spec_sb.set)
        self.spec_tree.pack(side="left", fill="both", expand=True)
        spec_sb.pack(side="right", fill="y")

        # 右：原稿与识别结果
        right = ttk.Frame(panes, padding=4)
        panes.add(right, weight=1)
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="② 原稿", style="Step.TLabel").pack(side="left")
        ttk.Button(top, text="选择 Word 文件…", command=self._choose_file).pack(side="left", padx=10)
        self.file_label = ttk.Label(top, text="尚未选择（仅支持 .docx）", foreground="#666")
        self.file_label.pack(side="left")
        ttk.Label(right, text="每段被识别成了什么。黄色 = 程序不太确定；识别错了就双击“类型”那一格修改。",
                  foreground="#666").pack(anchor="w", pady=(6, 4))

        frame = ttk.Frame(right)
        frame.pack(fill="both", expand=True)
        self.doc_tree = ttk.Treeview(frame, columns=["类型", "内容"], show="headings")
        self.doc_tree.heading("类型", text="类型")
        self.doc_tree.heading("内容", text="内容")
        self.doc_tree.column("类型", width=90, stretch=False, anchor="center")
        self.doc_tree.column("内容", width=480)
        self.doc_tree.tag_configure("low", background="#fff3b0")
        self.doc_tree.tag_configure("fixed", foreground="#888")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.doc_tree.yview)
        self.doc_tree.configure(yscrollcommand=sb.set)
        self.doc_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.doc_tree.bind("<Double-1>", self._edit_role)
        self.doc_msg = ttk.Label(right, foreground="#b9770e", wraplength=600, justify="left")
        self.doc_msg.pack(anchor="w", pady=4)

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
            pass  # 保存失败不影响使用

    def _req(self) -> str:
        return self.req_text.get("1.0", "end-1c")

    def _on_req_change(self, _event=None):
        if not self.req_text.edit_modified():
            return
        self.req_text.edit_modified(False)
        if self._parse_job:
            self.after_cancel(self._parse_job)
        self._parse_job = self.after(400, self._reparse)  # 停止输入 0.4 秒后再解析

    def _reparse(self):
        self._parse_job = None
        self.spec = parse(self._req())
        self.spec_tree.delete(*self.spec_tree.get_children())
        for role in DISPLAY_ROLES:
            d = describe(self.spec.styles[role])
            self.spec_tree.insert("", "end", text=ROLE_LABELS[role], values=[d[c] for c in SPEC_COLUMNS])
        self.spec_tree.insert("", "end", text="英文数字", values=[self.spec.latin_font, "", "", "", "", ""])
        top, bottom, left, right = self.spec.margins_mm
        self.spec_tree.insert("", "end", text="页边距",
                              values=[f"上{top:g} 下{bottom:g}", f"左{left:g}", f"右{right:g}", "毫米", "", ""])

        msgs = list(self.spec.problems)
        if self.fonts is not None:
            missing = sorted(f for f in fonts_used(self.spec) if f not in self.fonts)
            if missing:
                msgs.append("这台电脑没有安装：" + "、".join(missing)
                            + "。文件照样能生成，但打开时会用别的字体代替显示；请安装字体，或把要求改成已安装的字体名。")
        self.spec_msg.configure(text="\n".join(msgs))
        self._save_requirements()

    def _reset_requirements(self):
        if messagebox.askyesno(APP_NAME, "恢复成默认格式要求（规范图片）？当前内容会被覆盖。"):
            self.req_text.delete("1.0", "end")
            self.req_text.insert("1.0", DEFAULT_TEXT)

    def _import_requirements(self):
        path = filedialog.askopenfilename(filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if path:
            text = Path(path).read_bytes()
            for enc in ("utf-8-sig", "gbk"):  # 记事本在中文 Windows 上常存成 GBK
                try:
                    decoded = text.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                messagebox.showerror(APP_NAME, "无法读取这个文件的编码。")
                return
            self.req_text.delete("1.0", "end")
            self.req_text.insert("1.0", decoded)

    def _export_requirements(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="格式要求.txt",
                                            filetypes=[("文本文件", "*.txt")])
        if path:
            Path(path).write_text(self._req(), encoding="utf-8")

    # ---------- 原稿 ----------

    def _choose_file(self):
        path = filedialog.askopenfilename(filetypes=[("Word 文档", "*.docx")])
        if not path:
            return
        try:
            result = read_document(path)
        except Exception as e:  # 损坏的文件、加密文件、.doc 改了扩展名等
            messagebox.showerror(APP_NAME, f"读取失败：{e}\n\n如果是 .doc 或 .wps 文件，请先在 Word/WPS 里另存为 .docx。")
            return
        self.src_path, self.items, self.read_warnings = path, result.items, result.warnings
        self.file_label.configure(text=Path(path).name, foreground="#000")
        self._fill_doc_tree()
        self.go_btn.configure(state="normal" if self.items else "disabled")

    def _fill_doc_tree(self):
        self.doc_tree.delete(*self.doc_tree.get_children())
        for i, it in enumerate(self.items):
            tags = ("fixed",) if it.raw is not None else (("low",) if it.confidence == "low" else ())
            self.doc_tree.insert("", "end", iid=str(i), values=[it.label, it.text], tags=tags)
        low = sum(it.confidence == "low" for it in self.items)
        msgs = [f"共 {len(self.items)} 段，其中 {low} 段需要确认。" if low else f"共 {len(self.items)} 段。"]
        self.doc_msg.configure(text="\n".join(msgs + self.read_warnings))

    def _edit_role(self, event):
        row = self.doc_tree.identify_row(event.y)
        if not row or self.doc_tree.identify_column(event.x) != "#1":
            return
        item = self.items[int(row)]
        if item.raw is not None:
            return  # 表格不参与识别
        x, y, w, h = self.doc_tree.bbox(row, "#1")
        box = ttk.Combobox(self.doc_tree, values=list(LABEL_TO_ROLE), state="readonly")
        box.set(item.label)
        box.place(x=x, y=y, width=w + 40, height=h)
        box.focus_set()

        def commit(_e=None):
            role = LABEL_TO_ROLE[box.get()]
            item.role, item.confidence, item.note = role, "high", "手动指定"
            item.lead_len = (h3_lead_len(item.text) if role == Role.H3
                             else h2_lead_len(item.text) if role == Role.H2 else 0)
            self.doc_tree.item(row, values=[item.label, item.text], tags=())
            box.destroy()

        box.bind("<<ComboboxSelected>>", commit)
        box.bind("<FocusOut>", lambda e: box.destroy())
        box.bind("<Escape>", lambda e: box.destroy())

    # ---------- 生成 ----------

    def _generate(self):
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
        try:  # 高分屏下文字不发虚
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
