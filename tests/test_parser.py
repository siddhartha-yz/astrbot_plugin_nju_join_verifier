import pytest

from nju_join_verifier.parser import AnswerParseError, parse_join_answer


def test_plus():
    p = parse_join_answer("Major+Alice+123456")
    assert (p.major, p.name, p.student_id, p.format) == ("Major", "Alice", "123456", "plus")


def test_space():
    p = parse_join_answer("Major Alice 123456")
    assert p.name == "Alice" and p.format == "space"


def test_compact_rejected():
    with pytest.raises(AnswerParseError) as exc:
        parse_join_answer("MajorAlice123456")
    assert exc.value.code == "ambiguous_compact"


def test_bad_id_rejected():
    with pytest.raises(AnswerParseError) as exc:
        parse_join_answer("Major+Alice+abc")
    assert exc.value.code == "invalid_student_id"


def test_compact_cjk_generates_verified_name_candidates():
    p = parse_join_answer("\u4e13\u4e1a\u5f20\u4e09123456")
    assert p.format == "compact_candidates"
    assert p.student_id == "123456"
    assert "\u5f20\u4e09" in p.name_candidates
