import pytest

from nju_join_verifier.llm_parser import parse_llm_name, prepare_llm_fallback
from nju_join_verifier.parser import AnswerParseError


def test_prepare_llm_fallback_removes_student_id():
    item = prepare_llm_fallback("问题：专业+姓名+学号\n答案：人工智能张三12345678")
    assert item.student_id == "12345678"
    assert item.context == "人工智能张三"
    assert "12345678" not in item.context


def test_prepare_llm_fallback_supports_fullwidth_digits():
    item = prepare_llm_fallback("人工智能张三１２３４５６７８")
    assert item.student_id == "12345678"
    assert item.context == "人工智能张三"


def test_prepare_llm_fallback_rejects_multiple_ids():
    with pytest.raises(AnswerParseError) as exc:
        prepare_llm_fallback("人工智能张三12345678 87654321")
    assert exc.value.code == "llm_student_id_ambiguous"


def test_parse_llm_name_requires_extractable_substring():
    assert parse_llm_name("张三", "人工智能张三") == "张三"
    with pytest.raises(AnswerParseError) as exc:
        parse_llm_name("李四", "人工智能张三")
    assert exc.value.code == "llm_not_extractable"


def test_parse_llm_name_rejects_explanation():
    with pytest.raises(AnswerParseError):
        parse_llm_name("姓名是张三", "人工智能张三")
