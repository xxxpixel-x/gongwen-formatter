"""表格的结构：哪几行是表头、每个单元格占哪几列、底纹和文字格式，以及表格标题（“表1 ××”）的识别。

排版和预览共用。表格默认保持原稿的样子：读取时只把“原稿里看得到、但输出文档里没有了来源”
的格式（表格样式里的边框、底纹）写到表格上，见 apply_table_style。
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from docx.oxml.ns import qn
from lxml import etree

from .classify import CN_NUM, END_PUNCT, Item, Role, clean

OFF = ("0", "false", "off")

# OOXML 规定的子元素顺序：插入新元素时要放在这些元素前面
TBLPR_AFTER_BORDERS = ("shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription")
TCPR_AFTER_SHD = ("noWrap", "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark",
                  "headers", "cellIns", "cellDel", "cellMerge", "tcPrChange")


def _on(el) -> bool:
    return el is not None and el.get(qn("w:val"), "true") not in OFF


def insert_before(parent, child, later: tuple[str, ...]):
    for node in parent:
        if etree.QName(node).localname in later:
            node.addprevious(child)
            return
    parent.append(child)


def rows(tbl) -> list:
    return tbl.findall(qn("w:tr"))


def header_rows(tbl) -> set[int]:
    """Word 里设了“标题行重复”的行算表头；都没设时，多于一行的表格取第一行。"""
    trs = rows(tbl)
    marked = set()
    for i, tr in enumerate(trs):
        if not _on(tr.find(f"{qn('w:trPr')}/{qn('w:tblHeader')}")):
            break   # 标题行只能从第一行起连续设置
        marked.add(i)
    if marked:
        return marked
    return {0} if len(trs) > 1 else set()


def paragraph_text(p) -> str:
    parts = []
    for node in p.iter(qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")):
        if node.tag == qn("w:t"):
            parts.append(node.text or "")
        else:
            parts.append("\t" if node.tag == qn("w:tab") else "\n")
    return "".join(parts)


def cell_text(tc) -> str:
    """单元格里每段一行；嵌套表格的文字也拼进来。"""
    lines = [clean(paragraph_text(p)) for p in tc.iter(qn("w:p"))]
    return "\n".join(line for line in lines if line)


def has_borders(tbl) -> bool:
    for tag in ("w:tblBorders", "w:tcBorders"):
        for borders in tbl.iter(qn(tag)):
            if any(edge.get(qn("w:val")) not in (None, "none", "nil") for edge in borders):
                return True
    return False


def border_color(tbl) -> str | None:
    """第一条看得见的边框的颜色（如 "CCCCCC"）；自动颜色返回 None。预览用。"""
    for tag in ("w:tcBorders", "w:tblBorders"):
        for borders in tbl.iter(qn(tag)):
            for edge in borders:
                if edge.get(qn("w:val")) not in (None, "none", "nil"):
                    color = edge.get(qn("w:color"))
                    return color.upper() if color and color != "auto" else None
    return None


def alignment(tbl) -> str:
    jc = tbl.find(f"{qn('w:tblPr')}/{qn('w:jc')}")
    val = jc.get(qn("w:val")) if jc is not None else "left"
    return {"center": "center", "right": "right", "end": "right"}.get(val, "left")


def column_widths(tbl) -> list[int]:
    """各列宽度（1/20 磅）。"""
    return [int(g.get(qn("w:w")) or 0) for g in tbl.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")]


# ---------- 底纹 ----------

def _shd_fill(shd) -> str | None:
    """底纹颜色（如 "D9E2F3"）；没有底纹返回 None。图案底纹按前景色近似。"""
    if shd is None:
        return None
    val, fill, color = shd.get(qn("w:val")), shd.get(qn("w:fill")), shd.get(qn("w:color"))
    if val == "solid" and color and color != "auto":
        return color.upper()
    if fill and fill.lower() != "auto" and val != "nil":
        return fill.upper()
    return None


def cell_fill(tc) -> str | None:
    return _shd_fill(tc.find(f"{qn('w:tcPr')}/{qn('w:shd')}"))


# ---------- 表格样式 ----------

# tblLook 的十六进制写法（Word 2007）里各开关的位
_LOOK_BITS = {"firstRow": 0x20, "lastRow": 0x40, "firstColumn": 0x80, "lastColumn": 0x100,
              "noHBand": 0x200, "noVBand": 0x400}


def _look(tblpr) -> dict[str, bool]:
    look = tblpr.find(qn("w:tblLook")) if tblpr is not None else None
    if look is None:   # Word 新建表格的默认值 04A0
        return {"firstRow": True, "lastRow": False, "firstColumn": True, "lastColumn": False,
                "noHBand": False, "noVBand": True}
    hexval = look.get(qn("w:val"))
    out = {}
    for key, bit in _LOOK_BITS.items():
        attr = look.get(qn("w:" + key))
        if attr is not None:
            out[key] = attr not in OFF
        elif hexval:
            out[key] = bool(int(hexval, 16) & bit)
        else:
            out[key] = False
    return out


def style_chain(tbl, styles_el) -> list:
    """表格样式 → basedOn → …（近的在前）。"""
    style_el = tbl.find(f"{qn('w:tblPr')}/{qn('w:tblStyle')}")
    style_id = style_el.get(qn("w:val")) if style_el is not None else None
    by_id = {st.get(qn("w:styleId")): st for st in styles_el.findall(qn("w:style"))}
    chain = []
    while style_id in by_id and by_id[style_id] not in chain:
        chain.append(by_id[style_id])
        based = by_id[style_id].find(qn("w:basedOn"))
        style_id = based.get(qn("w:val")) if based is not None else None
    return chain


def _from_chain(chain, path: str):
    for style in chain:
        node = style.find(path)
        if node is not None:
            return node
    return None


def _cond(chain, kind: str, path: str):
    """条件格式（首行、隔行……）里的某个元素，如 path = "w:tcPr/w:shd"。"""
    for style in chain:
        for cond in style.findall(qn("w:tblStylePr")):
            if cond.get(qn("w:type")) == kind:
                node = cond.find("/".join(qn(p) for p in path.split("/")))
                if node is not None:
                    return node
    return None


def cell_conditions(tbl, chain) -> dict[tuple[int, int], list[str]]:
    """每个单元格适用的条件格式，按 Word 的优先级从低到高：隔行 < 首尾列 < 首尾行。"""
    tblpr = tbl.find(qn("w:tblPr"))
    look = _look(tblpr)
    band_el = _from_chain(chain, f"{qn('w:tblPr')}/{qn('w:tblStyleRowBandSize')}")
    band_size = max(1, int(band_el.get(qn("w:val")))) if band_el is not None else 1
    trs = rows(tbl)
    out = {}
    for r, tr in enumerate(trs):
        tcs = tr.findall(qn("w:tc"))
        body_row = r - (1 if look["firstRow"] else 0)
        for c in range(len(tcs)):
            kinds = []
            if not look["noHBand"] and body_row >= 0:
                kinds.append("band1Horz" if (body_row // band_size) % 2 == 0 else "band2Horz")
            if look["firstColumn"] and c == 0:
                kinds.append("firstCol")
            if look["lastColumn"] and c == len(tcs) - 1:
                kinds.append("lastCol")
            if look["firstRow"] and r == 0:
                kinds.append("firstRow")
            if look["lastRow"] and r == len(trs) - 1 and r > 0:
                kinds.append("lastRow")
            out[(r, c)] = kinds
    return out


def cond_props(chain, kinds: list[str], tag: str) -> list:
    """某个单元格适用的条件格式里的 w:rPr 或 w:pPr，优先级高的在前。"""
    found = (_cond(chain, k, tag) for k in reversed(kinds))
    return [node for node in found if node is not None]


def apply_table_style(tbl, styles_el) -> None:
    """原稿表格的边框、底纹、隔行色常常来自表格样式（如“网格型”“浅色底纹”），
    输出文档里没有这个样式，它们会消失。读取时把样式里的效果直接写到表格和单元格上。"""
    tblpr = tbl.find(qn("w:tblPr"))
    chain = style_chain(tbl, styles_el)
    if tblpr is None or not chain:
        return
    if tblpr.find(qn("w:tblBorders")) is None:
        borders = _from_chain(chain, f"{qn('w:tblPr')}/{qn('w:tblBorders')}")
        if borders is not None:
            insert_before(tblpr, copy.deepcopy(borders), TBLPR_AFTER_BORDERS)

    whole = _from_chain(chain, f"{qn('w:tcPr')}/{qn('w:shd')}")
    conditions = cell_conditions(tbl, chain)
    for r, tr in enumerate(rows(tbl)):
        for c, tc in enumerate(tr.findall(qn("w:tc"))):
            # 整表 < 条件格式；单元格自己设的底纹最优先
            found = [whole] + [_cond(chain, k, "w:tcPr/w:shd") for k in conditions[(r, c)]]
            found = [s for s in found if s is not None]   # lxml 元素没有子节点时为假，不能用 or 取值
            fill = _shd_fill(found[-1]) if found else None
            if fill is None:
                continue
            tcpr = tc.find(qn("w:tcPr"))
            if tcpr is None:
                tcpr = etree.Element(qn("w:tcPr"))
                tc.insert(0, tcpr)
            own = tcpr.find(qn("w:shd"))
            if own is not None:
                if _shd_fill(own) is not None:
                    continue
                tcpr.remove(own)
            new = etree.Element(qn("w:shd"))
            new.set(qn("w:val"), "clear")
            new.set(qn("w:color"), "auto")
            new.set(qn("w:fill"), fill)
            insert_before(tcpr, new, TCPR_AFTER_SHD)


# ---------- 单元格网格（预览用） ----------

@dataclass
class Cell:
    text: str
    row: int
    col: int
    colspan: int = 1
    rowspan: int = 1
    head: bool = False
    fill: str | None = None
    # 单元格里第一段文字的格式（读取时已把继承来的格式写在文字上）
    font: str | None = None
    size: float | None = None
    bold: bool = False
    color: str | None = None
    align: str | None = None


def _text_look(tc) -> dict:
    for p in tc.iter(qn("w:p")):
        run = next((r for r in p.iter(qn("w:r")) if clean(paragraph_text(r))), None)
        if run is None:
            continue
        rpr = run.find(qn("w:rPr"))
        fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
        sz = rpr.find(qn("w:sz")) if rpr is not None else None
        color = rpr.find(qn("w:color")) if rpr is not None else None
        jc = p.find(f"{qn('w:pPr')}/{qn('w:jc')}")
        color_val = color.get(qn("w:val")) if color is not None else None
        return dict(
            font=fonts.get(qn("w:eastAsia")) if fonts is not None else None,
            size=int(sz.get(qn("w:val"))) / 2 if sz is not None else None,
            bold=_on(rpr.find(qn("w:b"))) if rpr is not None else False,
            color=color_val if color_val and color_val != "auto" else None,
            align={"center": "center", "right": "right", "end": "right", "both": "both",
                   "distribute": "both"}.get(jc.get(qn("w:val")), "left") if jc is not None else "left",
        )
    return {}


def cells(tbl) -> list[Cell]:
    """按网格列出单元格；横向合并记为 colspan，纵向合并记为 rowspan。"""
    heads = header_rows(tbl)
    out: list[Cell] = []
    top: dict[int, Cell] = {}   # 每列当前纵向合并的起始单元格
    for r, tr in enumerate(rows(tbl)):
        before = tr.find(f"{qn('w:trPr')}/{qn('w:gridBefore')}")
        col = int(before.get(qn("w:val"))) if before is not None else 0
        for tc in tr.findall(qn("w:tc")):
            tcpr = tc.find(qn("w:tcPr"))
            span_el = tcpr.find(qn("w:gridSpan")) if tcpr is not None else None
            span = int(span_el.get(qn("w:val"))) if span_el is not None else 1
            vmerge = tcpr.find(qn("w:vMerge")) if tcpr is not None else None
            if vmerge is not None and vmerge.get(qn("w:val")) != "restart" and col in top:
                top[col].rowspan += 1
            else:
                cell = Cell(cell_text(tc), r, col, span, head=r in heads, fill=cell_fill(tc), **_text_look(tc))
                out.append(cell)
                for c in range(col, col + span):
                    top.pop(c, None)
                if vmerge is not None:
                    top[col] = cell
            col += span
    return out


# ---------- 表格标题 ----------

# “表1 ××”“表 2-1：××”“续表3”“表一 ××”；“表彰大会”“表格说明”不算
RE_CAPTION = re.compile(rf"^(?:续)?表\s*(?:\d+(?:\s*[-－—.．]\s*\d+)?|[{CN_NUM}]+)(?![\d{CN_NUM}])")
_NAMED_TABLE = re.compile(r"(?:表|清单|一览|名单|明细|统计|汇总)$")


def _neighbour_table(p) -> str | None:
    """段落紧挨着表格（中间只隔空段）时返回 "before"/"after"（表格在段落的哪一边）。"""
    for direction, step in (("after", "getnext"), ("before", "getprevious")):
        node = getattr(p, step)()
        while node is not None:
            name = etree.QName(node).localname
            if name == "tbl":
                return direction
            if name != "p" or clean(paragraph_text(node)):
                break
            node = getattr(node, step)()
    return None


def mark_captions(items: list[Item], elements: list) -> None:
    """紧挨着表格、以“表1”这类编号开头的段落是表格标题；表格正上方以“表”“明细”等结尾的短行，
    很可能也是表格标题，标为待确认。elements 与 items 一一对应（表格或拆出的软回车行为 None）。"""
    for it, el in zip(items, elements):
        if el is None or it.raw is not None or len(it.text) > 40 or it.text[-1] in END_PUNCT:
            continue
        side = _neighbour_table(el)
        if side is None:
            continue
        if RE_CAPTION.match(it.text):
            it.role, it.lead_len, it.number_role, it.fixed = Role.TABLE_CAPTION, 0, None, ""
            it.confidence, it.confirmed, it.note = "high", True, ""
        elif side == "after" and _NAMED_TABLE.search(it.text) and it.role in (Role.BODY, Role.H1, Role.SUBTITLE):
            it.role, it.lead_len, it.number_role = Role.TABLE_CAPTION, 0, None
            it.confidence, it.confirmed = "low", False
            it.note = "紧挨在表格上方、以“表”“明细”等结尾，猜测为表格标题"
