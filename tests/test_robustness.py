"""格式混乱、编号混写和模板回读的回归检查。"""
import pytest

from gongwen_formatter.classify import Role, check_numbering, classify


@pytest.mark.parametrize("text", [
    "附件材料请于月底前报送。", "附件说明了本次工作的具体要求。", "附件1中的信息仅供参考。",
])
def test_attachment_word_in_body_does_not_start_attachment_section(text):
    items = classify(["通知", "正文。", text, "1.下一项工作"])
    assert items[2].role == Role.BODY
    assert items[3].role == Role.H3


@pytest.mark.parametrize("label", ["附件", "附件：", "附件1", "附件 1：报名表", "附 件："])
def test_explicit_attachment_label(label):
    items = classify(["通知", "正文。", label, "1.报名表"])
    assert items[2].role == Role.ATTACH_LABEL
    assert items[3].role == Role.ATTACH_ITEM


@pytest.mark.parametrize("prefix", ["1.", "（1）", "1）"])
@pytest.mark.parametrize("body, expected", [
    ("工作安排:提交材料，说明：逾期不收。", "工作安排:"),
    ("请及时办理。咨询电话：12345。", ""),
    ("请及时办理，咨询电话：12345。", ""),
    ("上午9:30开始办理。", ""),
])
def test_numbered_heading_does_not_include_body_or_time(prefix, body, expected):
    item = classify(["通知", prefix + body])[1]
    assert item.text[:item.lead_len] == prefix + expected


def test_dunhao_before_digits_is_a_number_not_a_decimal():
    item = classify(["通知", "1、2026年重点工作"])[1]
    assert item.role == Role.H3
    assert item.text == "1. 2026年重点工作"
    assert item.lead_len == 3
    assert classify(["通知", item.text])[1].role == Role.H3


def test_numbered_body_keeps_sequence_context():
    items = classify(["通知", "一、工作要求", "（一）工作目标", "（二）请按时提交材料。", "（三）保障措施"])
    assert items[3].role == Role.BODY
    assert check_numbering(items) == []


@pytest.mark.parametrize("text", [
    "（一）上午9:30开始办理。", "（一）会议时间为9：30，请准时参加。",
])
def test_time_colon_is_not_h2_heading_boundary(text):
    assert classify(["通知", text])[1].role == Role.BODY
