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
    # 没写的一级标题仍按规范图片（黑体三号），但继承正文的行距
    h1 = s.styles[Role.H1]
    assert (h1.font, h1.size, h1.line, h1.line_multiple) == ("黑体", 16, None, 1.5)


def test_partial_requirements_keep_defaults():
    """只写了部分类型时，标题、正文、一级标题仍按规范图片。"""
    s = parse("日期（楷体_GB2312，三号，右对齐，右缩进1字符）\n英文、数字TimesNewRoman")
    assert s.problems == []
    assert (s.styles[Role.TITLE].font, s.styles[Role.TITLE].size) == ("方正小标宋_GBK", 22)
    assert s.styles[Role.BODY].first_indent == 2
    assert s.styles[Role.H1].font == "黑体"
    assert s.styles[Role.DATE].font == "楷体_GB2312"


def test_reports_what_it_cannot_understand():
    s = parse("一级标题：楷体，四号，蓝色\n随便写一行")
    assert any("蓝色" in p for p in s.problems)
    assert any("随便写一行" in p for p in s.problems)
    assert s.styles[Role.H1].font == "楷体" and s.styles[Role.H1].size == 14


def test_meeting_record_requirements():
    """会议记录规范：页眉页脚、厘米、28.8 磅、四五级标题。"""
    s = parse("标题（方正小标宋简体，二号，居中，行距固定值35磅）\n"
              "正文（方正仿宋_GB2312，三号，首行缩进2字符，段前0行，段后0行，行距固定值28.8磅）\n"
              "一级标题（黑体，三号，左对齐，顶格）\n二级标题（楷体_GB2312，三号，不加粗）\n"
              "三级标题（方正仿宋_GB2312，三号，加粗）\n四级标题（方正仿宋_GB2312，三号）\n"
              "英文、数字：Times New Roman\n"
              "页边距：上 3.7cm、下 3.5cm、左 2.8cm、右 2.6cm\n页眉 1.5cm，页脚 2.8cm")
    assert s.problems == []
    assert s.margins_mm == (37, 35, 28, 26)
    assert (s.header_mm, s.footer_mm) == (15, 28)
    assert s.styles[Role.TITLE].line == 35 and s.styles[Role.H1].line == 28.8
    assert s.styles[Role.H2].bold is False
    assert s.styles[Role.H4].font == "方正仿宋_GB2312"
    assert s.styles[Role.H5].font == "方正仿宋_GB2312"   # 没写的五级标题跟正文一样


def test_header_footer_variants():
    s = parse("页边距：上37毫米，下35毫米，左28毫米，右26毫米，页眉15毫米，页脚28毫米")
    assert s.problems == [] and (s.header_mm, s.footer_mm) == (15, 28)
    s = parse("页眉页脚：页眉距边界1.5厘米，页脚距边界2.8厘米")
    assert s.problems == [] and (s.header_mm, s.footer_mm) == (15, 28)
    assert parse("").header_mm is None
