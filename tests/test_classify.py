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


def test_inner_spaces_removed():
    from gongwen_formatter.classify import clean
    assert clean("　　金融与统   计学院，2026 年") == "金融与统计学院，2026 年"
    assert clean("Times New Roman 字体") == "Times New Roman 字体"


def test_level4_level5():
    items = classify(["标题", "一、总体要求", "（一）工作目标", "1.组织：成立小组。",
                      "（1）人员安排：由办公室负责。", "1）报名", "具体内容。"])
    assert [it.role for it in items[1:]] == [Role.H1, Role.H2, Role.H3, Role.H4, Role.H5, Role.BODY]
    h4 = items[4]
    assert h4.text[: h4.lead_len] == "（1）人员安排："


def test_decimal_is_not_heading():
    assert classify(["标题", "3.5万元用于采购设备。"])[1].role == Role.BODY


def test_numbering_normalized():
    from gongwen_formatter.classify import check_numbering
    items = classify(["标题", "一.总体", "(一)、目标", "1、组织", "(1)人员", "1)报名", "正文。"])
    assert [it.text for it in items[1:6]] == ["一、总体", "（一）目标", "1.组织", "（1）人员", "1）报名"]
    msgs = check_numbering(items)
    assert len(msgs) == 1 and "“1、”→“1.”" in msgs[0]


def test_numbering_problems():
    from gongwen_formatter.classify import check_numbering
    items = classify(["标题", "一、总体", "1.组织", "2.分工", "三、保障", "（一）经费", "（三）场地", "正文。"])
    msgs = check_numbering(items)
    assert len(msgs) == 3
    assert "第 3 段" in msgs[0] and "（一）" in msgs[0]     # 一、下面直接用了 1.，只提醒一次
    assert "第 5 段" in msgs[1] and "第 2 个" in msgs[1]    # 一、之后是 三、
    assert "第 7 段" in msgs[2] and "第 2 个" in msgs[2]    # （一）之后是 （三）
    ok = classify(["标题", "一、总体", "（一）目标", "（二）任务", "二、保障", "（一）经费"])
    assert check_numbering(ok) == []
