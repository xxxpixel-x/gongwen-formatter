"""格式要求解析测试。"""
from gongwen_formatter.classify import Role
from gongwen_formatter.spec import DEFAULT_TEXT, parse


def test_default_matches_spec_image():
    s = parse(DEFAULT_TEXT)
    assert s.problems == []
    t, b = s.styles[Role.TITLE], s.styles[Role.BODY]
    assert (t.font, t.size, t.align) == ("方正小标宋_GBK", 22, "center")
    assert (b.font, b.size, b.first_indent, b.line, b.align) == ("仿宋_GB2312", 16, 2, 28, "both")
    assert (s.styles[Role.H1].font, s.styles[Role.H1].first_indent) == ("黑体", 0)
    h2 = s.styles[Role.H2]
    assert (h2.font, h2.bold) == ("楷体_GB2312", True)
    assert s.latin_font == "Times New Roman"
    assert s.margins_mm == (37, 35, 28, 26)


def test_image_literal_lines():
    """直接照抄图片上的写法也能看懂。"""
    s = parse("标题（方正小标宋_GBK，二号，居中）\n"
              "正文（仿宋_GB2312，三号，英文、数字TimesNewRoman）首行缩进2字符，行距固定值28磅，两端对齐。\n"
              "一级标题（黑体三号）\n二级标题（楷体_GB2312,三号,加粗）")
    assert s.problems == []
    assert s.styles[Role.H1].font == "黑体" and s.styles[Role.H1].size == 16
    assert s.latin_font == "Times New Roman"


def test_free_style_and_inheritance():
    s = parse("正文：宋体 小四 1.5倍行距\n标题 黑体 18磅 加粗")
    b, t = s.styles[Role.BODY], s.styles[Role.TITLE]
    assert (b.font, b.size, b.line, b.line_multiple) == ("宋体", 12, None, 1.5)
    assert (t.font, t.size, t.bold, t.align) == ("黑体", 18, True, "center")
    # 没写的一级标题沿用正文字体、字号
    assert (s.styles[Role.H1].font, s.styles[Role.H1].size) == ("宋体", 12)


def test_reports_what_it_cannot_understand():
    s = parse("一级标题：楷体，四号，蓝色\n随便写一行")
    assert any("蓝色" in p for p in s.problems)
    assert any("随便写一行" in p for p in s.problems)
    assert s.styles[Role.H1].font == "楷体" and s.styles[Role.H1].size == 14
