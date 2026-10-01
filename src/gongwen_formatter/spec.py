"""格式要求解析：把用户输入的文字要求变成每类段落的具体格式。

支持的写法（每行一条，顺序随意）：

    标题（方正小标宋_GBK，二号，居中）
    正文：仿宋_GB2312，三号，首行缩进2字符，行距固定值28磅，两端对齐
    一级标题：黑体 三号 顶格
    英文、数字：Times New Roman
    页边距：上37毫米，下35毫米，左28毫米，右26毫米

冒号或括号前面是“段落类型”，后面是格式。没写的段落类型和没写的项
都沿用默认要求（规范图片），正文的行距会被其他段落继承。看不懂的片段会原样报告出来，
不会悄悄忽略。
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, fields

from .classify import Role, ROLE_LABELS

# ---------- 数据结构 ----------


@dataclass
class Style:
    font: str = "仿宋_GB2312"
    size: float = 16.0            # 磅；三号 = 16 磅
    bold: bool = False
    align: str = "both"           # both 两端对齐 / left / center / right
    first_indent: float = 0.0     # 首行缩进，单位：字符
    right_indent: float = 0.0     # 右缩进，单位：字符（落款、日期用）
    line: float | None = 28.0     # 固定行距（磅）；None 表示按倍数
    line_multiple: float = 1.0    # line 为 None 时生效：1 = 单倍行距，1.5 = 1.5 倍
    before: float = 0.0           # 段前（磅）
    after: float = 0.0            # 段后（磅）


@dataclass
class Spec:
    styles: dict[Role, Style]
    latin_font: str = "Times New Roman"
    margins_mm: tuple[float, float, float, float] = (37, 35, 28, 26)  # 上 下 左 右
    problems: list[str] = field(default_factory=list)  # 没看懂的片段


SIZE_NAMES = {
    "初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18,
    "三号": 16, "小三": 15, "四号": 14, "小四": 12, "五号": 10.5, "小五": 9,
    "六号": 7.5, "小六": 6.5,
}
PT_TO_NAME = {v: k for k, v in SIZE_NAMES.items()}

ALIGN_NAMES = {"两端对齐": "both", "居中": "center", "左对齐": "left",
               "右对齐": "right", "居右": "right", "居左": "left", "靠右": "right"}
ALIGN_LABELS = {"both": "两端对齐", "center": "居中", "left": "左对齐", "right": "右对齐"}

# 段落类型的各种叫法 → Role。长的写在前面，避免“标题”抢先匹配“一级标题”
ROLE_NAMES = [
    ("一级标题", Role.H1), ("二级标题", Role.H2), ("三级标题", Role.H3),
    ("副标题", Role.SUBTITLE), ("大标题", Role.TITLE), ("主标题", Role.TITLE),
    ("文件标题", Role.TITLE), ("标题", Role.TITLE),
    ("附件名称", Role.ATTACH_ITEM), ("附件说明", Role.ATTACH_LABEL), ("附件", Role.ATTACH_LABEL),
    ("落款", Role.SIGNATURE), ("署名", Role.SIGNATURE), ("发文单位", Role.SIGNATURE),
    ("成文日期", Role.DATE), ("日期", Role.DATE),
    ("正文", Role.BODY),
]

# ---------- 默认要求：规范图片 ----------

DEFAULT_TEXT = """\
标题（方正小标宋_GBK，二号，居中）
正文（仿宋_GB2312，三号，英文、数字Times New Roman）首行缩进2字符，行距固定值28磅，两端对齐
一级标题（黑体，三号，顶格）
二级标题（楷体_GB2312，三号，加粗，首行缩进2字符）
三级标题（仿宋_GB2312，三号，加粗，首行缩进2字符）
副标题（仿宋_GB2312，三号，居中）
附件（仿宋_GB2312，三号，首行缩进2字符）
附件名称（仿宋_GB2312，三号，首行缩进2字符）
落款（仿宋_GB2312，三号，右对齐，右缩进1字符）
日期（仿宋_GB2312，三号，右对齐，右缩进1字符）
页边距：上37毫米，下35毫米，左28毫米，右26毫米
"""

# ---------- 解析 ----------

FONT_RE = re.compile(
    r"(方正[^\s，,、；;：:）)（(]+"
    r"|(?:华文|微软|思源)?[^\s，,、；;：:）)（(\d]*?(?:宋体|仿宋|楷体|黑体|小标宋|雅黑|隶书|魏碑)"
    r"(?:[_-]?(?:GB2312|GBK|GB18030|简体|简|繁体))?)"
)
LATIN_RE = re.compile(
    r"(?:英文|数字|西文|字母)(?:[、和与及/]*(?:英文|数字|西文|字母))*"
    r"\s*(?:用|使用|为|采用|：|:)?\s*([A-Za-z][A-Za-z ]*[A-Za-z])"
)
NUM = r"(\d+(?:\.\d+)?)"


def _line_height(st: Style) -> float:
    """“段前 1 行”换算成磅：固定行距时就是行距值，否则约为字号的 1.3 倍。"""
    return st.line or st.size * 1.3


def _parse_attrs(text: str, st: Style, problems: list[str], where: str) -> None:
    """把一段格式描述（不含段落类型）写进 st。"""
    def take(pattern, fn):
        nonlocal text
        m = re.search(pattern, text)
        if m:
            fn(m)
            text = text[: m.start()] + "，" + text[m.end():]
        return m

    # 顺序有讲究：先取掉“行距28磅”里的数字，再找字号，免得把 28 磅当字号
    take(r"(?:行距)?(?:设置?为)?固定值?\s*" + NUM + r"\s*(?:磅|pt)|行距\s*" + NUM + r"\s*(?:磅|pt)",
         lambda m: setattr(st, "line", float(m.group(1) or m.group(2))))
    def multiple(v):
        st.line, st.line_multiple = None, v
    take(r"单倍行距", lambda m: multiple(1.0))
    take(r"双倍行距", lambda m: multiple(2.0))
    take(NUM + r"\s*倍行距|行距\s*" + NUM + r"\s*倍?",
         lambda m: multiple(float(m.group(1) or m.group(2))))
    take(r"段前\s*" + NUM + r"\s*(行|磅|pt)",
         lambda m: setattr(st, "before", float(m.group(1)) * (_line_height(st) if m.group(2) == "行" else 1)))
    take(r"段后\s*" + NUM + r"\s*(行|磅|pt)",
         lambda m: setattr(st, "after", float(m.group(1)) * (_line_height(st) if m.group(2) == "行" else 1)))
    take(r"首行缩进\s*" + NUM + r"\s*(?:个)?字符?", lambda m: setattr(st, "first_indent", float(m.group(1))))
    take(r"首行缩进(?!\s*\d)", lambda m: setattr(st, "first_indent", 2.0))
    take(r"右缩进\s*" + NUM + r"\s*(?:个)?字符?", lambda m: setattr(st, "right_indent", float(m.group(1))))
    take(r"顶格|不缩进|无缩进", lambda m: setattr(st, "first_indent", 0.0))
    take(r"不加粗|取消加粗", lambda m: setattr(st, "bold", False))
    take(r"加粗|粗体", lambda m: setattr(st, "bold", True))
    for name, val in ALIGN_NAMES.items():
        take(name, lambda m, v=val: setattr(st, "align", v))
    take("|".join(sorted(SIZE_NAMES, key=len, reverse=True)),
         lambda m: setattr(st, "size", float(SIZE_NAMES[m.group(0)])))
    take(NUM + r"\s*(?:磅|pt)", lambda m: setattr(st, "size", float(m.group(1))))
    take(FONT_RE.pattern, lambda m: setattr(st, "font", m.group(0)))

    leftover = re.sub(r"[，,、；;。：:（）()\s]|字体|字号|格式", "", text)
    if leftover:
        problems.append(f"{where}：没看懂“{leftover}”")


def parse(text: str, with_defaults: bool = True) -> Spec:
    """with_defaults=True：先套用默认要求（规范图片），再用用户写的内容逐项覆盖。
    这样用户只写了几行时，没写到的段落类型仍然按规范图片排，而不是退回到仿宋三号。"""
    if with_defaults and text is not DEFAULT_TEXT:
        text = DEFAULT_TEXT + "\n" + text
    base = Style()
    spec = Spec(styles={})
    problems = spec.problems
    pending: list[tuple[Role, str, str]] = []

    for raw in text.splitlines():
        line = raw.strip().strip("。")
        if not line or line.startswith("#"):
            continue

        # 英文数字字体（可以出现在任何一行里）
        m = LATIN_RE.search(line)
        if m:
            spec.latin_font = m.group(1).strip()
            if spec.latin_font.replace(" ", "").lower() == "timesnewroman":
                spec.latin_font = "Times New Roman"
            line = line[: m.start()] + line[m.end():]
            if re.fullmatch(r"[，,、；;：:（）()\s]*", line):
                continue

        if line.startswith("页边距"):
            for key, idx in (("上", 0), ("下", 1), ("左", 2), ("右", 3)):
                mm = re.search(key + r"\s*" + NUM + r"\s*(毫米|mm|厘米|cm)", line)
                if mm:
                    v = float(mm.group(1)) * (10 if mm.group(2) in ("厘米", "cm") else 1)
                    margins = list(spec.margins_mm)
                    margins[idx] = v
                    spec.margins_mm = tuple(margins)
            continue

        role = next((r for name, r in ROLE_NAMES if line.startswith(name)), None)
        if role is None:
            problems.append(f"“{line[:12]}”：开头不是可识别的段落类型（如 标题、正文、一级标题）")
            continue
        name = next(n for n, r in ROLE_NAMES if line.startswith(n))
        pending.append((role, line[len(name):], name))

    # 先处理正文：它的行距、字体是其他段落的默认值
    pending.sort(key=lambda x: x[0] != Role.BODY)
    for role, attrs, name in pending:
        if role == Role.BODY:
            st = base
        else:
            st = copy.deepcopy(spec.styles.get(role) or _role_default(role, base))
        _parse_attrs(attrs, st, problems, name)
        spec.styles[role] = st

    for role in Role:
        spec.styles.setdefault(role, _role_default(role, base))
    spec.styles[Role.BODY] = base
    return spec


def _role_default(role: Role, body: Style) -> Style:
    """没写要求的段落类型：从正文推出一个合理的默认值。"""
    st = copy.deepcopy(body)
    if role in (Role.TITLE, Role.SUBTITLE):
        st.align, st.first_indent = "center", 0
    elif role == Role.H1:
        st.first_indent = 0
    elif role in (Role.H2, Role.H3):
        st.bold = True
    elif role in (Role.SIGNATURE, Role.DATE):
        st.align, st.first_indent, st.right_indent = "right", 0, 1
    return st


# ---------- 反向：把格式写成人能看的文字（界面里的“解析结果”表格） ----------

def describe(st: Style) -> dict[str, str]:
    size = PT_TO_NAME.get(st.size, f"{st.size:g}磅")
    indent = f"首行缩进{st.first_indent:g}字符" if st.first_indent else "顶格"
    if st.right_indent:
        indent += f"，右缩进{st.right_indent:g}字符"
    return {
        "字体": st.font,
        "字号": size,
        "加粗": "加粗" if st.bold else "",
        "对齐": ALIGN_LABELS.get(st.align, st.align),
        "缩进": indent,
        "行距": f"固定值{st.line:g}磅" if st.line else
                ("单倍行距" if st.line_multiple == 1 else f"{st.line_multiple:g}倍行距"),
    }


DISPLAY_ROLES = [Role.TITLE, Role.SUBTITLE, Role.H1, Role.H2, Role.H3, Role.BODY,
                 Role.ATTACH_LABEL, Role.ATTACH_ITEM, Role.SIGNATURE, Role.DATE]


def fonts_used(spec: Spec) -> set[str]:
    return {spec.styles[r].font for r in DISPLAY_ROLES} | {spec.latin_font}
