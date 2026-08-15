from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_STUDENT_ID_RE = re.compile(r"\d{6,20}")
_TRIM_CHARS = " \t\r\n+＋/／,，;；:：-_—|｜()（）[]【】{}<>《》"
_NAME_SEPARATORS = {"·", "•", "・"}


class LLMParseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParsedIdentity:
    name: str
    student_id: str


def _valid_name(name: str) -> bool:
    if not 2 <= len(name) <= 30:
        return False
    if not name[0].isalpha() or not name[-1].isalpha():
        return False
    previous_separator = False
    for char in name:
        if char in _NAME_SEPARATORS:
            if previous_separator:
                return False
            previous_separator = True
            continue
        if not char.isalpha():
            return False
        previous_separator = False
    return True


def extract_answer_text(comment: str) -> str:
    """Return only the applicant's answer, normalized for full-width digits."""

    text = comment.translate(_FULLWIDTH_DIGITS).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise LLMParseError("empty")

    match = re.search(r"(?:^|\n)\s*答案\s*[:：]\s*(.+)\Z", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    else:
        nonempty = [line.strip() for line in text.split("\n") if line.strip()]
        if len(nonempty) > 1 and any("问题" in line for line in nonempty[:-1]):
            text = re.sub(r"^答案\s*[:：]\s*", "", nonempty[-1]).strip()

    text = text.strip(_TRIM_CHARS)
    if not text or len(text) > 300:
        raise LLMParseError("answer_length_invalid")
    return text


def _strip_optional_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else stripped


def parse_llm_identity(output: str, source_answer: str) -> ParsedIdentity:
    """Accept only an extractive name/student-ID pair returned by the model.

    The LLM is not trusted to invent or normalize arbitrary identity data. Both
    fields must be present in the original answer after conservative formatting
    normalization, and the external verification service remains authoritative.
    """

    text = output.strip()
    if text.upper() == "UNKNOWN":
        raise LLMParseError("llm_unknown")

    try:
        data = json.loads(_strip_optional_json_fence(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMParseError("llm_output_invalid") from exc

    if not isinstance(data, dict) or set(data) != {"name", "student_id"}:
        raise LLMParseError("llm_output_invalid")

    name = str(data.get("name", "")).strip()
    student_id = str(data.get("student_id", "")).translate(_FULLWIDTH_DIGITS).strip()
    if not _valid_name(name):
        raise LLMParseError("llm_name_invalid")
    if not _STUDENT_ID_RE.fullmatch(student_id):
        raise LLMParseError("llm_student_id_invalid")

    normalized_source = source_answer.translate(_FULLWIDTH_DIGITS)
    compact_source = re.sub(r"[\s+＋/／,，;；:：\-_—|｜()（）\[\]【】{}<>《》]", "", normalized_source)
    if name not in compact_source:
        raise LLMParseError("llm_name_not_extractable")
    if student_id not in compact_source:
        raise LLMParseError("llm_student_id_not_extractable")

    return ParsedIdentity(name=name, student_id=student_id)
