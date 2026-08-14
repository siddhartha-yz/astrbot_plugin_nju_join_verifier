from __future__ import annotations

import re
from dataclasses import dataclass

_STUDENT_ID_RE = re.compile(r"\d{6,20}")
_ANSWER_RE = re.compile(r"(?:^|\n)\s*答案\s*[:：]\s*(.+)\Z", re.DOTALL)


class AnswerParseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class JoinAnswer:
    major: str | None
    name_candidates: tuple[str, ...]
    student_id: str
    format: str

    @property
    def name(self) -> str:
        return self.name_candidates[0]


def _extract_answer(comment: str) -> str:
    text = comment.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise AnswerParseError("empty")

    match = _ANSWER_RE.search(text)
    if match:
        return match.group(1).strip()

    # Some OneBot implementations only expose the postscript/answer itself.
    nonempty = [line.strip() for line in text.split("\n") if line.strip()]
    if len(nonempty) > 1 and any("问题" in line for line in nonempty[:-1]):
        last = nonempty[-1]
        last = re.sub(r"^答案\s*[:：]\s*", "", last).strip()
        return last
    return text


def parse_join_answer(comment: str) -> JoinAnswer:
    """Parse common join-answer formats conservatively.

    Explicit separators produce one name. Compact CJK answers produce only a
    small set of suffix candidates; no compact candidate can trigger approval
    unless the external identity verifier confirms the same student ID/name pair.
    """

    answer = _extract_answer(comment)
    normalized = answer.replace("＋", "+").replace("／", "/")

    if "+" in normalized:
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        fmt = "plus"
    elif re.search(r"\s", normalized):
        parts = [part.strip() for part in re.split(r"\s+", normalized) if part.strip()]
        fmt = "space"
    elif "/" in normalized:
        parts = [part.strip() for part in normalized.split("/") if part.strip()]
        fmt = "slash"
    else:
        compact = re.fullmatch(r"(.+?)(\d{6,20})", normalized)
        if not compact:
            raise AnswerParseError("ambiguous_compact")
        stem, student_id = compact.group(1).strip(), compact.group(2)
        if not re.fullmatch(r"[\u4e00-\u9fff·]{4,}", stem):
            raise AnswerParseError("ambiguous_compact")
        candidates = tuple(stem[-length:] for length in (3, 2, 4, 5) if len(stem) > length)
        if not candidates:
            raise AnswerParseError("ambiguous_compact")
        return JoinAnswer(
            major=None,
            name_candidates=tuple(dict.fromkeys(candidates)),
            student_id=student_id,
            format="compact_candidates",
        )

    if len(parts) < 3:
        raise AnswerParseError("not_enough_fields")

    student_id = parts[-1]
    name = parts[-2]
    major = " ".join(parts[:-2]).strip()

    if not _STUDENT_ID_RE.fullmatch(student_id):
        raise AnswerParseError("invalid_student_id")
    if not major:
        raise AnswerParseError("empty_major")
    if not name or len(name) > 30 or any(ch.isdigit() for ch in name):
        raise AnswerParseError("invalid_name")

    return JoinAnswer(
        major=major,
        name_candidates=(name,),
        student_id=student_id,
        format=fmt,
    )
