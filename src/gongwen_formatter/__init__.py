"""gongwen-formatter：按输入的格式要求给 Word 公文自动排版。"""
from .classify import Item, Role, ROLE_LABELS, classify
from .spec import DEFAULT_TEXT, Spec, Style, parse
from .render import read_document, render

__version__ = "0.5.1"


def format_file(src: str, out: str, requirements: str = DEFAULT_TEXT):
    """一步到位：原稿 + 格式要求文字 → 排好版的新文件。返回 (识别结果, 提醒列表)。"""
    spec = parse(requirements)
    result = read_document(src)
    render(result.items, spec, out, source_path=src)
    return result.items, result.warnings + spec.problems
