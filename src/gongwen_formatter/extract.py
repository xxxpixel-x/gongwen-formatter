"""从一份“已经排好版的公文”里读出格式，写成格式要求文字。

结果不直接拿去排版，而是填进界面的输入框让用户核对、修改——
模板本身可能有不统一的地方（比如有几段漏设了行距），用户要能看见并纠正。

每个属性按 Word 的规则逐层查找：段落/文字上直接设置的 → 段落样式（含继承链）
→ 文档默认值 → 主题字体。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from .classify import Role, classify, clean
from .spec import ALIGN_LABELS, PT_TO_NAME

# 输出时每类段落用的名字（必须是 spec.ROLE_NAMES 认识的写法）
ROLE_WORDS = {
    Role.TITLE: "标题", Role.SUBTITLE: "副标题", Role.H1: "一级标题", Role.H2: "二级标题",
    Role.H3: "三级标题", Role.BODY: "正文", Role.ATTACH_LABEL: "附件",
    Role.ATTACH_ITEM: "附件名称", Role.SIGNATURE: "落款", Role.DATE: "日期",
}
ORDER = list(ROLE_WORDS)
# 模板里缺少某类段落时，从正文格式推算（与规范图片一致的习惯做法）
DERIVE = {
    Role.SUBTITLE: dict(align="center", indent=0.0, bold=False),
    Role.ATTACH_LABEL: dict(bold=False),
    Role.ATTACH_ITEM: dict(bold=False),
    Role.SIGNATURE: dict(align="right", indent=0.0, right_indent=1.0, bold=False),
    Role.DATE: dict(align="right", indent=0.0, right_indent=1.0, bold=False),
}
FIELD_LABELS = {"font": "字体", "size": "字号", "bold": "加粗", "align": "对齐",
                "indent": "缩进", "right_indent": "缩进", "line": "行距", "multiple": "行距",
                "before": "段前段后", "after": "段前段后"}
JC = {"both": "both", "justify": "both", "distribute": "both", "center": "center",
      "left": "left", "start": "left", "right": "right", "end": "right"}


@dataclass(frozen=True)
class Look:
    """一段文字看起来的样子。frozen 是为了能放进 Counter 里数“哪种最多”。"""
    font: str | None
    latin: str | None
    size: float
    bold: bool
    align: str
    indent: float
    right_indent: float
    line: float | None        # 固定行距（磅）
    multiple: float | None    # 倍数行距
    before: float
    after: float


class _Resolver:
    """按 Word 的继承规则查出某段、某个 run 的实际格式。"""

    def __init__(self, doc):
        self.doc = doc
        styles = doc.styles.element
        dd = styles.find(qn("w:docDefaults"))
        self.default_rpr = dd.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}") if dd is not None else None
        self.default_ppr = dd.find(f"{qn('w:pPrDefault')}/{qn('w:pPr')}") if dd is not None else None
        self.theme = self._theme_fonts()

    def _theme_fonts(self) -> dict[str, str]:
        """主题字体：样式里写 eastAsiaTheme="minorEastAsia" 时，真实字体在 theme1.xml 里。"""
        fonts: dict[str, str] = {}
        for rel in self.doc.part.rels.values():
            if rel.reltype.endswith("/theme"):
                root = etree.fromstring(rel.target_part.blob)
                ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
                for kind in ("major", "minor"):
                    node = root.find(f".//a:{kind}Font", ns)
                    if node is None:
                        continue
                    latin = node.find("a:latin", ns)
                    ea = node.find("a:ea", ns)
                    hans = node.find("a:font[@script='Hans']", ns)
                    ea_face = (ea.get("typeface") if ea is not None else "") or \
                              (hans.get("typeface") if hans is not None else "")
                    fonts[f"{kind}EastAsia"] = ea_face
                    fonts[f"{kind}HAnsi"] = fonts[f"{kind}Ascii"] = latin.get("typeface") if latin is not None else ""
        return fonts

    def _style_chain(self, paragraph):
        """段落样式 → basedOn → … → 默认段落样式。"""
        chain, style = [], paragraph.style
        while style is not None and len(chain) < 20:
            chain.append(style.element)
            style = style.base_style
        return chain

    def _levels(self, paragraph, tag, run_rpr=None):
        """从近到远列出各层的 rPr 或 pPr。"""
        levels = []
        if run_rpr is not None:
            levels.append(run_rpr)
        if tag == "w:pPr" and paragraph._p.pPr is not None:
            levels.append(paragraph._p.pPr)
        for st in self._style_chain(paragraph):
            el = st.find(qn(tag))
            if el is not None:
                levels.append(el)
        default = self.default_rpr if tag == "w:rPr" else self.default_ppr
        if default is not None:
            levels.append(default)
        return levels

    def _font(self, rprs, slot: str) -> str | None:
        for rpr in rprs:
            fonts = rpr.find(qn("w:rFonts"))
            if fonts is None:
                continue
            theme = fonts.get(qn(f"w:{slot}Theme"))
            if theme and self.theme.get(theme):
                return self.theme[theme]
            name = fonts.get(qn(f"w:{slot}"))
            if name:
                return name
        return None

    @staticmethod
    def _first(levels, tag, attr):
        for el in levels:
            node = el.find(qn(tag))
            if node is not None and node.get(qn(attr)) is not None:
                return node.get(qn(attr))
        return None

    def look(self, paragraph, run) -> Look:
        rprs = self._levels(paragraph, "w:rPr", run._r.rPr if run is not None else None)
        pprs = self._levels(paragraph, "w:pPr")

        size = float(self._first(rprs, "w:sz", "w:val") or 21) / 2  # Word 默认五号 10.5 磅
        bold = False
        for rpr in rprs:
            b = rpr.find(qn("w:b"))
            if b is not None:
                bold = b.get(qn("w:val"), "true") not in ("0", "false", "off")
                break

        align = JC.get(self._first(pprs, "w:jc", "w:val") or "left", "left")

        chars = self._first(pprs, "w:ind", "w:firstLineChars")
        twips = self._first(pprs, "w:ind", "w:firstLine")
        if chars and int(chars):
            indent = int(chars) / 100
        elif twips and int(twips):
            indent = int(twips) / 20 / size
        else:
            indent = 0.0
        rchars = self._first(pprs, "w:ind", "w:rightChars")
        rtwips = self._first(pprs, "w:ind", "w:right")
        right = int(rchars) / 100 if rchars and int(rchars) else \
            (int(rtwips) / 20 / size if rtwips and int(rtwips) else 0.0)

        line_val = self._first(pprs, "w:spacing", "w:line")
        rule = self._first(pprs, "w:spacing", "w:lineRule") or "auto"
        line = multiple = None
        if line_val:
            if rule in ("exact", "atLeast"):
                line = int(line_val) / 20
            else:
                multiple = int(line_val) / 240
        before = int(self._first(pprs, "w:spacing", "w:before") or 0) / 20
        after = int(self._first(pprs, "w:spacing", "w:after") or 0) / 20

        return Look(self._font(rprs, "eastAsia"), self._font(rprs, "ascii"), size, bold, align,
                    _half(indent), _half(right), line, multiple, before, after)


def _half(x: float) -> float:
    return round(x * 2) / 2


def _size_text(pt: float) -> str:
    return PT_TO_NAME.get(pt, f"{pt:g}磅")


def _describe(look: Look, role: Role) -> str:
    parts = []
    if look.font:
        parts.append(look.font)
    parts.append(_size_text(look.size))
    if role == Role.H3 or look.bold:  # 三级标题始终写明是否加粗（只作用于冒号前）
        parts.append("加粗" if look.bold else "不加粗")
    parts.append(ALIGN_LABELS[look.align])
    parts.append(f"首行缩进{look.indent:g}字符" if look.indent else "顶格")
    if look.right_indent:
        parts.append(f"右缩进{look.right_indent:g}字符")
    if look.line:
        parts.append(f"行距固定值{look.line:g}磅")
    elif look.multiple and look.multiple != 1:
        parts.append(f"{look.multiple:g}倍行距")
    else:
        parts.append("单倍行距")
    if look.before:
        parts.append(f"段前{look.before:g}磅")
    if look.after:
        parts.append(f"段后{look.after:g}磅")
    return f"{ROLE_WORDS[role]}（{'，'.join(parts)}）"


@dataclass
class Extracted:
    text: str                  # 填进输入框的格式要求
    found: list[Role]          # 模板里识别到的段落类型
    notes: list[str]           # 给用户看的说明（也已经以 # 注释写进 text）


def extract(path: str) -> Extracted:
    doc = Document(path)
    res = _Resolver(doc)
    paras = [p for p in doc.paragraphs if clean(p.text)]
    items = classify([clean(p.text) for p in paras])

    looks: dict[Role, Counter] = {}
    latin = Counter()
    for item, p in zip(items, paras):
        runs = [r for r in p.runs if r.text.strip()]
        if not runs:
            continue
        run = runs[0]  # 三级标题看冒号前（加粗部分），其他看第一段文字
        lk = res.look(p, run)
        looks.setdefault(item.role, Counter())[lk] += 1
        for r in runs:
            if any(ch.isascii() and ch.isalnum() for ch in r.text):
                latin[res.look(p, r).latin] += 1

    notes: list[str] = []
    by_role: dict[Role, str] = {}
    for role in ORDER:
        if role not in looks:
            continue
        counter = looks[role]
        best, n = counter.most_common(1)[0]
        total = sum(counter.values())
        if len(counter) > 1:
            diff = sorted({FIELD_LABELS[f] for other in counter if other != best
                           for f in FIELD_LABELS if getattr(other, f) != getattr(best, f)},
                          key=list(dict.fromkeys(FIELD_LABELS.values())).index)
            notes.append(f"{ROLE_WORDS[role]}：模板里有 {total - n} 段的{'、'.join(diff)}和其他段不一样，"
                         f"按多数（{n}/{total} 段）填写，请核对")
        by_role[role] = _describe(best, role)

    # 模板里没出现的类型：附件、落款等按模板正文推算（字体与全文一致）；标题类沿用默认要求
    derived, defaulted = [], []
    body = looks[Role.BODY].most_common(1)[0][0] if Role.BODY in looks else None
    for role in ORDER:
        if role in looks:
            continue
        if body is not None and role in DERIVE:
            by_role[role] = _describe(replace(body, **DERIVE[role]), role)
            derived.append(ROLE_WORDS[role])
        else:
            defaulted.append(ROLE_WORDS[role])
    if derived:
        notes.append("模板里没有" + "、".join(derived) + "，按模板正文的字体、字号推算")
    if defaulted:
        notes.append("模板里没有" + "、".join(defaulted) + "，沿用默认要求")

    lines = [by_role[r] for r in ORDER if r in by_role]
    latin_font = latin.most_common(1)[0][0] if latin else None
    if latin_font and latin_font.isascii():
        lines.append(f"英文、数字：{latin_font}")
    elif latin_font:
        notes.append(f"模板里英文、数字用的是 {latin_font}，按规范改为 Times New Roman")
        lines.append("英文、数字：Times New Roman")

    sect = doc.sections[0]
    mm = [round(v.mm) for v in (sect.top_margin, sect.bottom_margin, sect.left_margin, sect.right_margin)
          if v is not None]
    if len(mm) == 4:
        lines.append(f"页边距：上{mm[0]}毫米，下{mm[1]}毫米，左{mm[2]}毫米，右{mm[3]}毫米")

    header = [f"# 以下格式读取自模板《{Path(path).name}》，请核对后再排版"]
    header += [f"# {n}" for n in notes]
    return Extracted("\n".join(header + lines) + "\n", list(looks), notes)
