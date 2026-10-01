"""读取原稿 → 识别 → 按格式要求输出新文档。

输出是一份全新的 A4 文档，每一段的格式都直接写在段落上
（不依赖 Word 样式），这样在 Word 和 WPS 里打开都一样。
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from docx import Document
from docx.shared import Mm
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml import etree

from .classify import Item, Role, classify, clean, insert_blanks
from .spec import Spec, Style


@dataclass
class ReadResult:
    items: list[Item]
    warnings: list[str] = field(default_factory=list)


def read_document(path: str) -> ReadResult:
    """按原文顺序取出所有段落文字；表格原样保留；图片暂不支持，给出提醒。"""
    doc = Document(path)
    texts: list[str] = []
    slots: list[object] = []   # str 表示段落文字；lxml 元素表示表格
    warnings: list[str] = []

    for child in doc.element.body.iterchildren():
        tag = etree.QName(child).localname
        if tag == "p":
            p = Paragraph(child, doc)
            if child.findall(".//" + qn("w:drawing")) or child.findall(".//" + qn("w:pict")):
                warnings.append(f"“{clean(p.text)[:12] or '（图片段落）'}”附近有图片，当前版本会跳过图片。")
            num_id = child.find(f"{qn('w:pPr')}/{qn('w:numPr')}/{qn('w:numId')}")
            if num_id is not None and num_id.get(qn("w:val")) != "0":  # numId=0 表示“已关闭编号”
                warnings.append(f"“{clean(p.text)[:12]}”使用了 Word 自动编号，编号本身不会保留，请在原稿中改成手打编号。")
            # 段内软回车（Shift+Enter）拆成独立段落
            for line in p.text.split("\n"):
                line = clean(line)
                if line:
                    slots.append(line)
        elif tag == "tbl":
            slots.append(child)

    texts = [s for s in slots if isinstance(s, str)]
    classified = iter(classify(texts))
    items: list[Item] = []
    for s in slots:
        if isinstance(s, str):
            items.append(next(classified))
        else:
            items.append(Item(Role.BODY, "[表格]", raw=s, note="表格原样保留"))
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
    lead = item.lead_len if item.role in (Role.H2, Role.H3) else 0
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


def render(items: list[Item], spec: Spec, out_path: str, blanks: bool = True) -> None:
    doc = _setup_document(spec)
    sect = doc.element.body.find(qn("w:sectPr"))
    for item in insert_blanks(items) if blanks else items:
        el = copy.deepcopy(item.raw) if item.raw is not None else _paragraph(item, spec)
        sect.addprevious(el)

    props = doc.core_properties
    props.author = props.last_modified_by = ""
    props.title = next((i.text for i in items if i.role == Role.TITLE), "")
    doc.save(out_path)
