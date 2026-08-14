import pytest

from nju_join_verifier.llm_parser import (
    LLMParseError,
    extract_answer_text,
    parse_llm_identity,
)


def test_extract_answer_text_uses_answer_line_only():
    text = extract_answer_text("问题：姓名+学号\n答案：张三 12345678")
    assert text == "张三 12345678"


def test_extract_answer_text_normalizes_fullwidth_digits():
    assert extract_answer_text("张三１２３４５６７８") == "张三12345678"


def test_parse_identity_from_strict_json():
    parsed = parse_llm_identity(
        '{"name":"张三","student_id":"12345678"}',
        "张三 12345678",
    )
    assert parsed.name == "张三"
    assert parsed.student_id == "12345678"


def test_parse_identity_accepts_json_fence():
    parsed = parse_llm_identity(
        '```json\n{"name":"张三","student_id":"12345678"}\n```',
        "张三+12345678",
    )
    assert parsed.name == "张三"


def test_parse_identity_ignores_extra_major_but_requires_extractiveness():
    parsed = parse_llm_identity(
        '{"name":"张三","student_id":"12345678"}',
        "人工智能 张三 12345678",
    )
    assert parsed.name == "张三"
    with pytest.raises(LLMParseError) as exc:
        parse_llm_identity(
            '{"name":"李四","student_id":"12345678"}',
            "人工智能 张三 12345678",
        )
    assert exc.value.code == "llm_name_not_extractable"


def test_parse_identity_rejects_invented_student_id():
    with pytest.raises(LLMParseError) as exc:
        parse_llm_identity(
            '{"name":"张三","student_id":"87654321"}',
            "张三 12345678",
        )
    assert exc.value.code == "llm_student_id_not_extractable"


def test_parse_identity_rejects_extra_json_fields():
    with pytest.raises(LLMParseError) as exc:
        parse_llm_identity(
            '{"name":"张三","student_id":"12345678","major":"AI"}',
            "张三 12345678",
        )
    assert exc.value.code == "llm_output_invalid"


def test_parse_identity_rejects_explanation():
    with pytest.raises(LLMParseError) as exc:
        parse_llm_identity("姓名是张三，学号12345678", "张三 12345678")
    assert exc.value.code == "llm_output_invalid"


def test_parse_identity_rejects_unknown():
    with pytest.raises(LLMParseError) as exc:
        parse_llm_identity("UNKNOWN", "乱填")
    assert exc.value.code == "llm_unknown"
