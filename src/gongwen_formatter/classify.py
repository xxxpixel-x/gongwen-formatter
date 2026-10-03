"""段落角色识别：根据文字本身判断每一段是标题、一级标题、正文……

只看文字，不看原来的格式——用户的原稿格式往往是乱的，不可信。
每个判断都带一个 confidence（"high" / "low"），低置信度的段落
会在图形界面里高亮，提醒用户确认。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    TITLE = "title"              # 大标题
    SUBTITLE = "subtitle"        # 副标题（如“金融与统计学院·青年志愿者协会”）
    H1 = "h1"                    # 一、
    H2 = "h2"                    # （一）
    H3 = "h3"                    # 1.
    H4 = "h4"                    # （1）
    H5 = "h5"                    # 1）
    BODY = "body"                # 正文
    ATTACH_LABEL = "attach_label"  # 附件：
    ATTACH_ITEM = "attach_item"    # 附件下的 1. xxx
    SIGNATURE = "signature"      # 落款单位
    DATE = "date"                # 成文日期
    BLANK = "blank"              # 程序插入的空行


ROLE_LABELS = {
    Role.TITLE: "大标题",
    Role.SUBTITLE: "副标题",
    Role.H1: "一级标题",
    Role.H2: "二级标题",
    Role.H3: "三级标题",
    Role.H4: "四级标题",
    Role.H5: "五级标题",
    Role.BODY: "正文",
    Role.ATTACH_LABEL: "附件标识",
    Role.ATTACH_ITEM: "附件名称",
    Role.SIGNATURE: "落款",
    Role.DATE: "日期",
    Role.BLANK: "空行",
}

CN_NUM = "一二三四五六七八九十百零〇"
RE_H1 = re.compile(rf"^[{CN_NUM}]+[、，,.．]")
RE_H2 = re.compile(rf"^[（(][{CN_NUM}]+[）)]")
RE_H3 = re.compile(r"^\d{1,2}\s*[.．、](?!\d)\s*")   # (?!\d)：不把“3.5万元”当成编号
RE_H4 = re.compile(r"^[（(]\d{1,2}[）)]")
RE_H5 = re.compile(r"^\d{1,2}[）)]")
RE_ATTACH = re.compile(r"^附\s*件\s*\d*\s*[:：]?")
RE_DATE = re.compile(
    rf"^([0-9xX]{{4}}|[{CN_NUM}]{{4}})\s*年\s*[0-9xX{CN_NUM}]{{1,3}}\s*月"
    rf"\s*[0-9xX{CN_NUM}]{{1,3}}\s*日$"
)
END_PUNCT = "。！？；：，、.!?;:,"
SPACES = " \t\u3000\xa0"


@dataclass
class Item:
    """排版后文档里的一段。lead_len > 0 表示前 lead_len 个字用“标题格式”，其余用正文格式。"""
    role: Role
    text: str
    lead_len: int = 0
    confidence: str = "high"
    note: str = ""
    raw: object = field(default=None, repr=False)  # 表格等无需识别的原始 XML
    confirmed: bool = True       # 界面里：低置信度段落在用户确认前为 False
    fixed: str = ""              # 编号写法被统一过时的说明，如 “1、”→“1.”

    @property
    def label(self) -> str:
        return ROLE_LABELS[self.role]


# 两个中文字符（含中文标点）之间夹着的空格几乎都是误输入，例如“金融与统   计学院”
_CJK = r"\u3000-\u303f\u4e00-\u9fff\uff00-\uffef“”‘’—…·"
_INNER_SPACES = re.compile(rf"(?<=[{_CJK}])[ \t\u3000\xa0]+(?=[{_CJK}])")


def clean(text: str) -> str:
    """去掉首尾空格（含全角空格）和中文之间多余的空格——缩进由格式负责，不靠空格。"""
    text = text.strip(SPACES + "\r\n")
    return _INNER_SPACES.sub("", text)


def _short_heading_like(text: str, limit: int = 15) -> bool:
    return 0 < len(text) <= limit and text[-1] not in END_PUNCT


def h3_lead_len(text: str, num_re: re.Pattern = RE_H3) -> int:
    """“1. 思想政治引领：xxx” → 加粗到冒号；没有冒号就只加粗编号。四、五级标题同样处理。"""
    m = num_re.match(text)
    num_len = m.end() if m else 0
    colon = text.find("：", 0, 31)
    if colon == -1:
        colon = text.find(":", 0, 31)
    return colon + 1 if colon != -1 else num_len


def h2_lead_len(text: str) -> int:
    """（一）标题。正文…… 同段时只有标题部分用二级标题格式。"""
    stop = text.find("。")
    if stop != -1 and stop < len(text) - 1:
        return stop + 1
    return len(text)


def lead_len(role: Role, text: str) -> int:
    """标题和正文写在同一段时，前多少个字用标题格式；0 表示整段同一格式。"""
    if role == Role.H2:
        return h2_lead_len(text)
    if role in NUM_RE and role != Role.H1:
        return h3_lead_len(text, NUM_RE[role])
    return 0


# ---------- 层级序号：一、→（一）→1.→（1）→1） ----------

NUM_RE = {Role.H1: RE_H1, Role.H2: RE_H2, Role.H3: RE_H3, Role.H4: RE_H4, Role.H5: RE_H5}
LEVEL = {Role.H1: 1, Role.H2: 2, Role.H3: 3, Role.H4: 4, Role.H5: 5}
LEVEL_SAMPLE = {1: "一、", 2: "（一）", 3: "1.", 4: "（1）", 5: "1）"}

# 编号部分的标准写法：括号用全角、“1.”用半角点、编号后不再多一个顿号
_NORMALIZE = {
    Role.H1: (re.compile(rf"^([{CN_NUM}]+)\s*[、，,.．]"), r"\1、"),
    Role.H2: (re.compile(rf"^[（(]([{CN_NUM}]+)[）)][、，,.．]?"), r"（\1）"),
    Role.H3: (re.compile(r"^(\d{1,2})\s*[.．、](?!\d)"), r"\1."),
    Role.H4: (re.compile(r"^[（(](\d{1,2})[）)][、，,.．]?"), r"（\1）"),
    Role.H5: (re.compile(r"^(\d{1,2})[）)][、，,.．]?"), r"\1）"),
}


def normalize_number(role: Role, text: str) -> tuple[str, str]:
    """把编号改成标准写法，返回 (新文字, 改动说明)；本来就标准时说明为空。"""
    rule = _NORMALIZE.get(role)
    m = rule[0].match(text) if rule else None
    if not m:
        return text, ""
    new = m.expand(rule[1])
    if new == m.group(0):
        return text, ""
    return new + text[m.end():], f"“{m.group(0)}”→“{new}”"


def _cn_to_int(s: str) -> int | None:
    """一 → 1，十二 → 12，二十 → 20。看不懂返回 None。"""
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["〇"] = 0
    if s == "十":
        return 10
    if "十" in s:
        tens, _, ones = s.partition("十")
        t = digits.get(tens, None) if tens else 1
        o = digits.get(ones, None) if ones else 0
        return None if t is None or o is None else t * 10 + o
    return digits.get(s) if len(s) == 1 else None


def _number_of(role: Role, text: str) -> int | None:
    m = re.match(rf"^[（(]?([{CN_NUM}]+|\d+)", text)
    if not m or not NUM_RE[role].match(text):
        return None
    s = m.group(1)
    return int(s) if s.isdigit() else _cn_to_int(s)


def check_numbering(items: list[Item], limit: int = 8) -> list[str]:
    """检查层级序号：写法是否标准（已自动统一）、有没有跳级、编号是否连续。返回给用户看的提醒。"""
    fixed: list[str] = []
    problems: list[str] = []
    last: dict[int, int | None] = {}   # 每一级当前的编号；-1 表示没有编号的小标题
    for i, it in enumerate(items):
        if it.fixed and it.fixed not in fixed:
            fixed.append(it.fixed)
        level = LEVEL.get(it.role)
        if level is None:
            continue
        for deeper in range(level + 1, 6):
            last.pop(deeper, None)
        where = f"第 {i + 1} 段“{it.text[:12]}”"
        if level > 1 and (level - 1) not in last:
            problems.append(f"{where}：属于“{LEVEL_SAMPLE[level]}”这一级，但前面没有上一级“{LEVEL_SAMPLE[level - 1]}”，"
                            f"层级序号应依次为 一、→（一）→1.→（1）→1），请确认是否混用")
            last[level - 1] = -1   # 同一处缺上一级只提醒一次
        num = _number_of(it.role, it.text)
        prev = last.get(level)
        if num is not None and prev != -1:
            want = (prev or 0) + 1
            if num != want:
                problems.append(f"{where}：编号不连续，按顺序应为第 {want} 个")
        last[level] = num if num is not None else -1

    msgs = []
    if fixed:
        shown = "、".join(fixed[:limit]) + (f" 等 {len(fixed)} 处" if len(fixed) > limit else "")
        msgs.append(f"已把编号统一为标准写法：{shown}")
    msgs += problems[:limit]
    if len(problems) > limit:
        msgs.append(f"序号问题还有 {len(problems) - limit} 处未列出")
    return msgs


def classify(texts: list[str]) -> list[Item]:
    """输入：每段文字（已去掉空段）。输出：每段的角色。"""
    items = [Item(Role.BODY, t) for t in texts]
    n = len(items)
    if n == 0:
        return items

    # 1) 编号明确的标题；编号写法顺手统一（如“1、”→“1.”、“(1)”→“（1）”）
    for it in items:
        role = next((r for r, rx in NUM_RE.items() if rx.match(it.text)), None)
        if role is not None:
            it.text, it.fixed = normalize_number(role, it.text)
            it.role, it.lead_len = role, lead_len(role, it.text)
        elif RE_DATE.match(it.text):
            it.role = Role.DATE

    # 2) 大标题、副标题：第一段是标题；第二段很短、不以标点结尾、不是编号标题 → 副标题
    if items[0].role == Role.BODY:
        items[0].role = Role.TITLE
    else:
        items[0].confidence, items[0].note = "low", "第一段带编号，可能缺少大标题"
    if n > 1 and items[1].role == Role.BODY and _short_heading_like(items[1].text, 30):
        items[1].role, items[1].confidence = Role.SUBTITLE, "low"
        items[1].note = "根据“短、无标点结尾”判断为副标题"

    # 3) 附件区：从“附件：”开始，后面的 1. xxx 都是附件名称
    for i, it in enumerate(items):
        if RE_ATTACH.match(it.text) and len(it.text) <= 40:
            it.role, it.lead_len = Role.ATTACH_LABEL, 0
            j = i + 1
            while j < n and items[j].role == Role.H3:
                items[j].role, items[j].lead_len = Role.ATTACH_ITEM, 0
                j += 1
            break

    # 4) 落款：日期上方 1~2 行短句
    for i, it in enumerate(items):
        if it.role != Role.DATE:
            continue
        j = i - 1
        while j >= 0 and i - j <= 2 and items[j].role == Role.BODY \
                and _short_heading_like(items[j].text, 25):
            items[j].role = Role.SIGNATURE
            j -= 1

    # 5) 没有编号、但像小标题的短行（如图片里的“相关说明”）
    for i, it in enumerate(items):
        if i < 2 or it.role != Role.BODY or not _short_heading_like(it.text, 12):
            continue
        nxt = items[i + 1] if i + 1 < n else None
        if nxt is not None and nxt.role in (Role.BODY, Role.H2, Role.H3, Role.H4, Role.H5):
            it.role, it.confidence = Role.H1, "low"
            it.note = "没有编号，根据“短行 + 后面接正文”猜测为一级标题"

    return items


def insert_blanks(items: list[Item]) -> list[Item]:
    """按范例图片：附件前、落款前各空一行。"""
    out: list[Item] = []
    for i, it in enumerate(items):
        prev = items[i - 1].role if i else None
        if it.role == Role.ATTACH_LABEL or (
            it.role in (Role.SIGNATURE, Role.DATE)
            and prev not in (Role.SIGNATURE, Role.DATE, None)
        ):
            out.append(Item(Role.BLANK, ""))
        out.append(it)
    return out
