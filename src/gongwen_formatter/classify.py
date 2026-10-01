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
RE_H3 = re.compile(r"^\d{1,2}\s*[.．、]\s*")
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

    @property
    def label(self) -> str:
        return ROLE_LABELS[self.role]


def clean(text: str) -> str:
    """去掉首尾空格（含全角空格）——缩进由模板负责，不靠空格。"""
    return text.strip(SPACES + "\r\n")


def _short_heading_like(text: str, limit: int = 15) -> bool:
    return 0 < len(text) <= limit and text[-1] not in END_PUNCT


def h3_lead_len(text: str) -> int:
    """“1. 思想政治引领：xxx” → 加粗到冒号；没有冒号就只加粗编号。"""
    m = RE_H3.match(text)
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


def classify(texts: list[str]) -> list[Item]:
    """输入：每段文字（已去掉空段）。输出：每段的角色。"""
    items = [Item(Role.BODY, t) for t in texts]
    n = len(items)
    if n == 0:
        return items

    # 1) 编号明确的标题
    for it in items:
        t = it.text
        if RE_H1.match(t):
            it.role = Role.H1
        elif RE_H2.match(t):
            it.role, it.lead_len = Role.H2, h2_lead_len(t)
        elif RE_H3.match(t):
            it.role, it.lead_len = Role.H3, h3_lead_len(t)
        elif RE_DATE.match(t):
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
        if nxt is not None and nxt.role in (Role.BODY, Role.H2, Role.H3):
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
