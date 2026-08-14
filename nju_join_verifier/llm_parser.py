from __future__ import annotations

import re
from dataclasses import dataclass

from .parser import AnswerParseError

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_STUDENT_ID_RE = re.compile(r"\d{6,20}")
_NAME_RE = re.compile(r"[\u4e00-\u9fff·]{2,8}")
_TRIM_CHARS = " \t\r\n+＋/／,，;；:：-_—|｜()（）[]【】{}<>《》"


@dataclass(frozen=True, slots=True)
class LLMFallbackInput:
    context: str
    student_id: str


def _answer_only(comment: str) -> str:
    text = comment.translate(_FULLWIDTH_DIGITS).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise AnswerParseError("empty")
    match = re.search(r"(?:^|\n)\s*答案\s*[:：]\s*(.+)\Z", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    nonempty = [line.strip() for line in text.split("\n") if line.strip()]
    if len(nonempty) > 1 and any("问题" in line for line in nonempty[:-1]):
        return re.sub(r"^答案\s*[:：]\s*", "", nonempty[-1]).strip()
    return text


def prepare_llm_fallback(comment: str) -> LLMFallbackInput:
    """Extract one student ID locally and return only the non-ID text for LLM use.

    The student ID is deliberately excluded from ``context`` so the model never
    needs to receive it. A fallback is rejected when the answer contains zero or
    multiple plausible student-ID runs.
    """

    answer = _answer_only(comment)
    matches = list(_STUDENT_ID_RE.finditer(answer))
    if len(matches) != 1:
        raise AnswerParseError("llm_student_id_ambiguous")
    match = matches[0]
    student_id = match.group(0)
    context = (answer[: match.start()] + answer[match.end() :]).strip(_TRIM_CHARS)
    context = re.sub(r"\s+", " ", context).strip()
    if len(context) < 2 or len(context) > 100:
        raise AnswerParseError("llm_context_invalid")
    return LLMFallbackInput(context=context, student_id=student_id)


def parse_llm_name(output: str, source_context: str) -> str:
    """Accept only an extractive Chinese-name answer from the model."""

    text = output.strip()
    if text.upper() == "UNKNOWN":
        raise AnswerParseError("llm_unknown")
    text = text.strip("`'\"“”‘’ \t\r\n")
    if not _NAME_RE.fullmatch(text):
        raise AnswerParseError("llm_output_invalid")

    compact_source = re.sub(r"[\s+＋/／,，;；:：\-_—|｜()（）\[\]【】{}<>《》]", "", source_context)
    if text not in compact_source:
        raise AnswerParseError("llm_not_extractable")
    return text
