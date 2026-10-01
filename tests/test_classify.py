"""识别规则单元测试：用模板图片里的示例文字。"""
from gongwen_formatter.classify import Role, classify

SPEC = [
    "标题",
    "正文内容，首行缩进2字符。",
    "一、一级标题",
    "（一）二级标题",
    "正文内容。",
    "二、时间进度安排",
    "（一）10月1日-10月7日，筹备阶段",
    "相关说明",
    "正文仿宋三号。",
    "附件：",
    "1.正文仿宋三号",
    "2.正文仿宋三号",
    "金融与统计学院",
    "xxxx年xx月xx日",
]


def test_spec_image():
    roles = [it.role for it in classify(SPEC)]
    assert roles == [
        Role.TITLE, Role.BODY, Role.H1, Role.H2, Role.BODY, Role.H1, Role.H2,
        Role.H1, Role.BODY, Role.ATTACH_LABEL, Role.ATTACH_ITEM, Role.ATTACH_ITEM,
        Role.SIGNATURE, Role.DATE,
    ]


def test_h3_bold_lead():
    it = classify(["标题", "1. 思想政治引领：加强建设。"])[1]
    assert it.text[: it.lead_len] == "1. 思想政治引领："
    it = classify(["标题", "1. 统筹成立青春防非志愿者团，统领多所高校开展行动并获得报道。"])[1]
    assert it.text[: it.lead_len] == "1. "


def test_h2_inline_body():
    it = classify(["标题", "（一）工作目标。今年完成三项任务。"])[1]
    assert it.text[: it.lead_len] == "（一）工作目标。"
