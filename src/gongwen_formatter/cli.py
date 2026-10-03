"""命令行入口。

  gongwen-fmt 原稿.docx                         # 按默认要求（规范图片）排版
  gongwen-fmt 原稿.docx -r 我的要求.txt          # 按自己写的要求排版
  gongwen-fmt 原稿.docx --check                 # 只看识别结果和解析出的格式
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ROLE_LABELS, parse, read_document, render
from .spec import DEFAULT_TEXT, DISPLAY_ROLES, describe


def main(argv=None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 终端中文

    ap = argparse.ArgumentParser(prog="gongwen-fmt", description="按格式要求给 Word 公文自动排版")
    ap.add_argument("src", help="需要排版的原稿 .docx")
    ap.add_argument("-r", "--requirements", help="格式要求文本文件（不填则用默认要求）")
    ap.add_argument("-o", "--out", help="输出路径（默认：原稿名_已排版.docx）")
    ap.add_argument("--check", action="store_true", help="只打印识别结果，不生成文件")
    args = ap.parse_args(argv)

    text = Path(args.requirements).read_text(encoding="utf-8") if args.requirements else DEFAULT_TEXT
    spec = parse(text)
    result = read_document(args.src)

    print("格式要求解析结果：")
    for role in DISPLAY_ROLES:
        print(f"  {ROLE_LABELS[role]:<5}", "，".join(v for v in describe(spec.styles[role]).values() if v))
    hf = lambda v: "默认" if v is None else f"{v:g}"
    print(f"  英文数字 {spec.latin_font}；页边距 上下左右 {spec.margins_mm} 毫米；"
          f"页眉 {hf(spec.header_mm)}、页脚 {hf(spec.footer_mm)} 毫米\n")

    print("原稿识别结果：")
    for i, it in enumerate(result.items, 1):
        flag = "?" if it.confidence == "low" else " "
        print(f"{i:>3} {flag} [{it.label:<4}] {it.text[:30]}{'…' if len(it.text) > 30 else ''}"
              + (f"   ← {it.note}" if it.note else ""))
    for w in spec.problems + result.warnings:
        print("提醒：", w)
    if args.check:
        return 0

    out = args.out or str(Path(args.src).with_name(Path(args.src).stem + "_已排版.docx"))
    render(result.items, spec, out)
    print("\n已生成：", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
