"""读取原稿 → 识别 → 按格式要求输出新文档。

输出是一份全新的 A4 文档，每一段的格式都直接写在段落上
（不依赖 Word 样式），这样在 Word 和 WPS 里打开都一样。
"""
from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.image.exceptions import InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError
from docx.shared import Mm
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.text.paragraph import Paragraph
from lxml import etree

from .classify import Item, Role, check_numbering, classify, clean, insert_blanks
from .extract import _Resolver
from .spec import Spec, Style
from .table import (apply_table_style, cell_conditions, cond_props, header_rows, insert_before, mark_captions,
                    rows, style_chain)


@dataclass
class ReadResult:
    items: list[Item]
    warnings: list[str] = field(default_factory=list)


def _has_auto_numbering(paragraph: Paragraph) -> bool:
    """编号可来自直接格式或样式继承；显式 numId=0 会关闭继承的编号。"""
    levels = [paragraph._p.pPr]
    style, seen = paragraph.style, set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        levels.append(style.element.find(qn("w:pPr")))
        style = style.base_style
    for ppr in levels:
        if ppr is None:
            continue
        num_id = ppr.find(f"{qn('w:numPr')}/{qn('w:numId')}")
        if num_id is not None:
            return num_id.get(qn("w:val")) != "0"
    return False


def read_document(path: str) -> ReadResult:
    """按原文顺序取出所有段落文字；表格保持原样（格式要求里写了“表头”“表格”时只改写到的项）；
    图片暂不支持，给出提醒。"""
    doc = Document(path)
    texts: list[str] = []
    slots: list[object] = []   # str 表示段落文字；lxml 元素表示表格
    sources: list[object] = []  # 每个文字段落来自哪个 w:p（软回车拆出的多行为 None），用于找表格标题
    warnings: list[str] = []
    resolver = None

    for child in doc.element.body.iterchildren():
        tag = etree.QName(child).localname
        if tag == "p":
            p = Paragraph(child, doc)
            if child.findall(".//" + qn("w:drawing")) or child.findall(".//" + qn("w:pict")):
                warnings.append(f"“{clean(p.text)[:12] or '（图片段落）'}”附近有图片，当前版本会跳过图片。")
            if _has_auto_numbering(p):
                warnings.append(f"“{clean(p.text)[:12]}”使用了 Word 自动编号，编号本身不会保留，请在原稿中改成手打编号。")
            # 段内软回车（Shift+Enter）拆成独立段落
            lines = [line for line in (clean(x) for x in p.text.split("\n")) if line]
            slots += lines
            sources += [child if len(lines) == 1 else None] * len(lines)
        elif tag == "tbl":
            resolver = resolver or _Resolver(doc)
            _keep_table_look(child, doc, resolver)
            slots.append(child)

    texts = [s for s in slots if isinstance(s, str)]
    classified = classify(texts)
    mark_captions(classified, sources)
    classified = iter(classified)
    items: list[Item] = []
    for s in slots:
        if isinstance(s, str):
            items.append(next(classified))
        else:
            n_rows, n_cols = len(rows(s)), len(s.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}"))
            items.append(Item(Role.BODY, f"[表格 {n_rows}行×{n_cols}列]", raw=s, source_part=doc.part,
                              note="表格保持原样"))
    warnings += check_numbering(items)
    return ReadResult(items, warnings)


# ---------- 写出 ----------

def _el(tag: str, **attrs):
    el = etree.Element(qn(tag))
    for k, v in attrs.items():
        el.set(qn("w:" + k), str(v))
    return el


def _ppr(st: Style):
    """段落格式。子元素顺序必须符合 OOXML 规范，否则 Word 会报文件损坏。"""
    ppr = _el("w:pPr")
    ppr.append(_el("w:widowControl", val=0))
    ppr.append(_el("w:snapToGrid", val=0))   # 不对齐文档网格，固定行距才准确
    spacing = _el("w:spacing", before=round(st.before * 20), after=round(st.after * 20))
    if st.line:
        spacing.set(qn("w:line"), str(round(st.line * 20)))   # 单位：1/20 磅
        spacing.set(qn("w:lineRule"), "exact")
    else:
        spacing.set(qn("w:line"), str(round(st.line_multiple * 240)))
        spacing.set(qn("w:lineRule"), "auto")
    ppr.append(spacing)
    # 缩进按“字符”设置（firstLineChars=200 即 2 字符），同时给出等价的绝对值
    ind = _el("w:ind")
    ind.set(qn("w:firstLineChars"), str(round(st.first_indent * 100)))
    ind.set(qn("w:firstLine"), str(round(st.first_indent * st.size * 20)))
    if st.right_indent:
        ind.set(qn("w:rightChars"), str(round(st.right_indent * 100)))
        ind.set(qn("w:right"), str(round(st.right_indent * st.size * 20)))
    ppr.append(ind)
    ppr.append(_el("w:jc", val=st.align))
    return ppr


def _rpr(st: Style, latin: str, bold: bool):
    rpr = _el("w:rPr")
    # 中文用 eastAsia 字体；数字、英文用 ascii/hAnsi 字体（Times New Roman）
    fonts = _el("w:rFonts", ascii=latin, hAnsi=latin, eastAsia=st.font, cs=latin)
    # hint=eastAsia：引号、破折号这类中英共用的符号按中文字体显示
    fonts.set(qn("w:hint"), "eastAsia")
    rpr.append(fonts)
    if bold:
        rpr.append(_el("w:b"))
        rpr.append(_el("w:bCs"))
    half_points = round(st.size * 2)
    rpr.append(_el("w:sz", val=half_points))
    rpr.append(_el("w:szCs", val=half_points))
    return rpr


def _run(text: str, rpr):
    r = etree.Element(qn("w:r"))
    r.append(copy.deepcopy(rpr))
    t = etree.SubElement(r, qn("w:t"))
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _paragraph(item: Item, spec: Spec):
    st = spec.styles[item.role]
    body = spec.styles[Role.BODY]
    p = etree.Element(qn("w:p"))
    p.append(_ppr(st))
    if not item.text:
        return p
    lead = item.lead_len if item.role in (Role.H2, Role.H3, Role.H4, Role.H5) else 0
    if 0 < lead < len(item.text):
        # “1. 小标题：正文……”：前半段用标题格式，后半段用正文格式
        p.append(_run(item.text[:lead], _rpr(st, spec.latin_font, st.bold)))
        p.append(_run(item.text[lead:], _rpr(body, spec.latin_font, body.bold)))
    else:
        p.append(_run(item.text, _rpr(st, spec.latin_font, st.bold)))
    return p


def _setup_document(spec: Spec) -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    top, bottom, left, right = spec.margins_mm
    sec.top_margin, sec.bottom_margin = Mm(top), Mm(bottom)
    sec.left_margin, sec.right_margin = Mm(left), Mm(right)
    if spec.header_mm is not None:
        sec.header_distance = Mm(spec.header_mm)
    if spec.footer_mm is not None:
        sec.footer_distance = Mm(spec.footer_mm)

    # 默认样式也改掉，避免空段落、表格沿用 Calibri / 段后 8 磅
    body = spec.styles[Role.BODY]
    styles = doc.styles.element
    defaults = styles.find(qn("w:docDefaults"))
    if defaults is not None:
        styles.remove(defaults)
    defaults = _el("w:docDefaults")
    rpr_default = etree.SubElement(defaults, qn("w:rPrDefault"))
    rpr_default.append(_rpr(body, spec.latin_font, False))
    ppr_default = etree.SubElement(defaults, qn("w:pPrDefault"))
    ppr_default.append(_el("w:pPr"))
    ppr_default[0].append(_el("w:spacing", after=0, line=240, lineRule="auto"))
    styles.insert(0, defaults)

    # python-docx 自带的空白模板缺少 zoom 百分比，补上以通过严格校验
    zoom = doc.settings.element.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")

    for child in list(doc.element.body):
        if etree.QName(child).localname != "sectPr":
            doc.element.body.remove(child)
    return doc


# ---------- 表格 ----------
# 表格默认保持原样。原稿表格里的文字格式大多继承自原稿的样式（正文样式、表格样式、文档默认值），
# 输出文档里没有这些样式，直接复制会变成新文档的默认格式（仿宋三号、28 磅行距）。
# 所以读取时按 Word 的继承规则把格式算出来，直接写在每段、每段文字上。

_PPR_ORDER = ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
              "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
              "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
              "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
              "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
              "sectPr", "pPrChange")
_RPR_ORDER = ("rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
              "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
              "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
              "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath")
_SPACING_ATTRS = ("before", "beforeLines", "beforeAutospacing", "after", "afterLines", "afterAutospacing",
                  "line", "lineRule")
_IND_ATTRS = ("left", "start", "leftChars", "startChars", "right", "end", "rightChars", "endChars",
              "hanging", "hangingChars", "firstLine", "firstLineChars")
_RUN_COPIED = ("b", "bCs", "i", "iCs", "strike", "color", "u")


def _put(parent, child, order: tuple[str, ...]):
    """放进 parent，替换同名元素；位置符合 OOXML 规定的顺序。"""
    name = etree.QName(child).localname
    old = parent.find(child.tag)
    if old is not None:
        old.addprevious(child)
        parent.remove(old)
    else:
        insert_before(parent, child, order[order.index(name) + 1:])


def _merged_attrs(levels, tag: str, attrs: tuple[str, ...]) -> dict[str, str]:
    """各层里某元素的属性，近的优先，逐个属性合并。"""
    out: dict[str, str] = {}
    for el in levels:
        node = el.find(qn(tag))
        if node is None:
            continue
        for a in attrs:
            v = node.get(qn("w:" + a))
            if v is not None and a not in out:
                out[a] = v
    return out


def _with_table(res: _Resolver, paragraph: Paragraph, tag: str, own, table_levels: list, normal: bool) -> list:
    """段落/文字格式的各层，加上表格样式。Word 的规则：表格样式盖过“正文”样式，
    但不盖过其他段落样式。own 是段落或文字自己的 pPr/rPr，最优先。"""
    levels = res._levels(paragraph, tag, own if tag == "w:rPr" else None)
    default = res.default_rpr if tag == "w:rPr" else res.default_ppr
    tail = [levels.pop()] if levels and default is not None and levels[-1] is default else []
    direct = 1 if levels and levels[0] is own else 0
    if normal:
        levels = levels[:direct] + table_levels + levels[direct:]
    else:
        levels = levels + table_levels
    return levels + tail


def _bake_rpr(target, levels: list, res: _Resolver) -> None:
    """levels[0] 就是 target 自己。"""
    fonts = target.find(qn("w:rFonts"))
    if fonts is None:
        fonts = _el("w:rFonts")
        _put(target, fonts, _RPR_ORDER)
    for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
        name = res._font(levels, slot)
        if name:
            fonts.set(qn("w:" + slot), name)
        fonts.attrib.pop(qn(f"w:{slot}Theme"), None)
    for tag in _RUN_COPIED:
        if target.find(qn("w:" + tag)) is not None:
            continue
        node = next((n for n in (el.find(qn("w:" + tag)) for el in levels[1:]) if n is not None), None)
        if node is not None:
            _put(target, copy.deepcopy(node), _RPR_ORDER)
    size = res._first(levels, "w:sz", "w:val") or "21"   # Word 默认五号
    _put(target, _el("w:sz", val=size), _RPR_ORDER)
    _put(target, _el("w:szCs", val=res._first(levels, "w:szCs", "w:val") or size), _RPR_ORDER)


def _bake_paragraph(p, doc: Document, res: _Resolver, chain: list, kinds: list[str]) -> None:
    paragraph = Paragraph(p, doc)
    style = paragraph.style
    normal = style is None or style.element.get(qn("w:default")) in ("1", "true")

    def table_levels(tag):
        whole = [n for n in (st.find(qn(tag)) for st in chain) if n is not None]
        return cond_props(chain, kinds, tag) + whole

    ppr = p.get_or_add_pPr()
    levels = _with_table(res, paragraph, "w:pPr", ppr, table_levels("w:pPr"), normal)
    for tag, attrs in (("w:spacing", _SPACING_ATTRS), ("w:ind", _IND_ATTRS), ("w:jc", ("val",))):
        values = _merged_attrs(levels, tag, attrs)
        if values:
            _put(ppr, _el(tag, **values), _PPR_ORDER)

    mark = ppr.find(qn("w:rPr"))   # 段落标记的字号决定空单元格的行高
    if mark is None:
        mark = _el("w:rPr")
        _put(ppr, mark, _PPR_ORDER)
    runs = [mark] + [r for r in p.iter(qn("w:r"))]
    for r in runs:
        rpr = r if r is mark else r.find(qn("w:rPr"))
        if rpr is None:
            rpr = _el("w:rPr")
            r.insert(0, rpr)
        _bake_rpr(rpr, _with_table(res, paragraph, "w:rPr", rpr, table_levels("w:rPr"), normal), res)

    pstyle = ppr.find(qn("w:pStyle"))
    if pstyle is not None:   # 原稿的样式在输出文档里不存在或含义不同，格式已经写在段落上了
        ppr.remove(pstyle)


def _keep_table_look(tbl, doc: Document, res: _Resolver) -> None:
    """读取时调用：表格样式里的边框、底纹，以及继承来的文字格式，直接写到表格上。
    只改读进内存的原稿，不会保存回原稿文件。"""
    styles = doc.styles.element
    apply_table_style(tbl, styles)
    chain = style_chain(tbl, styles)
    conditions = cell_conditions(tbl, chain)
    for r, tr in enumerate(rows(tbl)):
        for c, tc in enumerate(tr.findall(qn("w:tc"))):
            for p in tc.iter(qn("w:p")):
                _bake_paragraph(p, doc, res, chain, conditions[(r, c)])


def _apply_table_fields(p, st: Style, fields: set[str]) -> None:
    """格式要求里写了“表头”“表格”时，只改写到的项。"""
    ppr = p.find(qn("w:pPr"))
    if ppr is None:
        ppr = _el("w:pPr")
        p.insert(0, ppr)
    if fields & {"line", "line_multiple", "before", "after"}:
        spacing = ppr.find(qn("w:spacing"))
        if spacing is None:
            spacing = _el("w:spacing")
            _put(ppr, spacing, _PPR_ORDER)
        if fields & {"line", "line_multiple"}:
            if st.line:
                spacing.set(qn("w:line"), str(round(st.line * 20)))
                spacing.set(qn("w:lineRule"), "exact")
            else:
                spacing.set(qn("w:line"), str(round(st.line_multiple * 240)))
                spacing.set(qn("w:lineRule"), "auto")
        for side in ("before", "after"):
            if side in fields:
                spacing.set(qn("w:" + side), str(round(getattr(st, side) * 20)))
                spacing.attrib.pop(qn(f"w:{side}Lines"), None)
    if fields & {"first_indent", "right_indent"}:
        ind = ppr.find(qn("w:ind"))
        if ind is None:
            ind = _el("w:ind")
            _put(ppr, ind, _PPR_ORDER)
        if "first_indent" in fields:
            ind.set(qn("w:firstLineChars"), str(round(st.first_indent * 100)))
            ind.set(qn("w:firstLine"), str(round(st.first_indent * st.size * 20)))
            for attr in ("hanging", "hangingChars"):
                ind.attrib.pop(qn("w:" + attr), None)
        if "right_indent" in fields:
            ind.set(qn("w:rightChars"), str(round(st.right_indent * 100)))
            ind.set(qn("w:right"), str(round(st.right_indent * st.size * 20)))
    if "align" in fields:
        _put(ppr, _el("w:jc", val=st.align), _PPR_ORDER)

    mark = ppr.find(qn("w:rPr"))
    for rpr in ([mark] if mark is not None else []) + [r.find(qn("w:rPr")) for r in p.iter(qn("w:r"))]:
        if rpr is None:
            continue
        if "font" in fields:
            fonts = rpr.find(qn("w:rFonts"))
            if fonts is None:
                fonts = _el("w:rFonts")
                _put(rpr, fonts, _RPR_ORDER)
            fonts.set(qn("w:eastAsia"), st.font)   # 只改中文字体，数字英文仍按原稿
            fonts.attrib.pop(qn("w:eastAsiaTheme"), None)
        if "size" in fields:
            for tag in ("w:sz", "w:szCs"):
                _put(rpr, _el(tag, val=round(st.size * 2)), _RPR_ORDER)
        if "bold" in fields:
            for tag in ("w:b", "w:bCs"):
                _put(rpr, _el(tag) if st.bold else _el(tag, val=0), _RPR_ORDER)


def _format_table(tbl, spec: Spec, max_width: int) -> None:
    """表格保持原样；写了“表头”“表格”时只改写到的项；比版心宽的表格按比例缩窄，免得超出纸面。"""
    if spec.table_fields:
        heads = header_rows(tbl)
        for i, tr in enumerate(rows(tbl)):
            role = Role.TABLE_HEAD if i in heads else Role.TABLE
            fields = spec.table_fields.get(role)
            if not fields:
                continue
            for p in tr.iter(qn("w:p")):
                _apply_table_fields(p, spec.styles[role], fields)

    tblpr = tbl.find(qn("w:tblPr"))
    tblw = tblpr.find(qn("w:tblW")) if tblpr is not None else None
    limit = max_width
    if tblw is not None and tblw.get(qn("w:type")) == "pct":
        # pct 以 1/50 % 为单位（5000 = 100%），也有写成“85%”的
        raw = tblw.get(qn("w:w")) or "5000"
        try:
            pct = float(raw[:-1]) * 50 if raw.endswith("%") else float(raw)
        except ValueError:
            pct = 5000
        limit = round(max_width * min(pct, 5000) / 5000)
    grid = tbl.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")
    total = sum(_twips(g) for g in grid)
    if total <= limit:
        return
    ratio = limit / total
    for w in [*grid, *tbl.iter(qn("w:tcW"))]:
        if w.get(qn("w:type")) in (None, "dxa"):
            w.set(qn("w:w"), str(int(_twips(w) * ratio)))
    if tblw is not None:
        tblw.set(qn("w:w"), str(limit))
        tblw.set(qn("w:type"), "dxa")


def _twips(el) -> int:
    try:
        return int(float(el.get(qn("w:w")) or 0))
    except ValueError:
        return 0


def ensure_distinct_paths(source: str, target: str) -> None:
    """GUI、命令行和库调用都不能通过同名、符号链接或硬链接覆盖原稿。"""
    src, out = Path(source), Path(target)
    if src.resolve() == out.resolve() or (src.exists() and out.exists() and src.samefile(out)):
        raise ValueError("不能覆盖原稿，请选择不同的输出文件名。")


def _copy_table(item: Item, doc: Document):
    """复制 XML 时同时迁移图片和外部链接，不能沿用原文档的关系编号。"""
    element = copy.deepcopy(item.raw)
    mapped: dict[str, str] = {}
    for node in element.iter():
        for attr in (qn("r:id"), qn("r:embed"), qn("r:link")):
            old_id = node.get(attr)
            if old_id is None:
                continue
            if old_id not in mapped:
                source = item.source_part
                if source is None or old_id not in source.rels:
                    raise ValueError("表格中的图片或链接缺少源关系，无法完整保存。")
                rel = source.rels[old_id]
                if rel.is_external:
                    new_id = doc.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
                elif rel.reltype == RT.IMAGE:
                    try:
                        new_id, _ = doc.part.get_or_add_image(BytesIO(rel.target_part.blob))
                    except (InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError) as exc:
                        raise ValueError("表格图片已损坏或格式暂不支持，请先转换为 PNG 或 JPEG。") from exc
                else:
                    raise ValueError("表格包含暂不支持的嵌入对象，无法完整保存；请先转换为普通文字或图片。")
                mapped[old_id] = new_id
            node.set(attr, mapped[old_id])
    return element


def _save_atomic(doc: Document, out_path: str) -> None:
    """先在目标目录写完临时文件，再替换；失败时已有输出保持不变。"""
    target = Path(out_path)
    with tempfile.NamedTemporaryFile(prefix=".gongwen-", suffix=".docx", dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        doc.save(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def render(items: list[Item], spec: Spec, out_path: str, blanks: bool = True,
           *, source_path: str | None = None) -> None:
    if source_path is not None:
        ensure_distinct_paths(source_path, out_path)
    doc = _setup_document(spec)
    sect = doc.element.body.find(qn("w:sectPr"))
    text_width = round((210 - spec.margins_mm[2] - spec.margins_mm[3]) * 1440 / 25.4)   # 版心宽，1/20 磅
    for item in insert_blanks(items) if blanks else items:
        if item.raw is not None:
            el = _copy_table(item, doc)
            _format_table(el, spec, text_width)
        else:
            el = _paragraph(item, spec)
        sect.addprevious(el)

    props = doc.core_properties
    props.author = props.last_modified_by = ""
    props.title = next((i.text for i in items if i.role == Role.TITLE), "")
    _save_atomic(doc, out_path)
