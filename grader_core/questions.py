from dataclasses import dataclass
from html.parser import HTMLParser
import re
from collections.abc import Sequence
from typing import Protocol

from grader_core.documents import extract_html_blocks


class QuestionMappingError(ValueError):
    """Raised when question HTML cannot be mapped to catalog questions."""


@dataclass(frozen=True)
class QuestionSection:
    question_id: str
    label: str
    text: str


class QuestionTarget(Protocol):
    id: str
    label: str
    max_points: int


_NUMBERED_QUESTION_PATTERN = re.compile(
    r"(?m)^\s*(?P<number>\d+)[.)]\s+"
)


class _VisibleQuestionParser(HTMLParser):
    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    _BLOCK_TAGS = _HEADING_TAGS | frozenset(
        {"article", "div", "li", "p", "section", "td", "th"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._current_parts: list[str] = []
        self._current_kind = "block"
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.casefold()
        if normalized_tag in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if normalized_tag in self._HEADING_TAGS:
            self._flush()
            self._current_kind = "heading"
        elif normalized_tag == "br":
            self._current_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in {"script", "style"}:
            self._ignored_depth = max(self._ignored_depth - 1, 0)
            return
        if self._ignored_depth:
            return
        if normalized_tag in self._BLOCK_TAGS:
            self._flush()
            self._current_kind = "block"

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._current_parts.append(data)

    def finish(self) -> list[tuple[str, str]]:
        self._flush()
        return self.blocks

    def _flush(self) -> None:
        raw_text = "".join(self._current_parts)
        self._current_parts.clear()
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
        text = "\n".join(line for line in lines if line)
        if text:
            self.blocks.append((self._current_kind, text))


def extract_question_sections(
    html: str,
    questions: Sequence[QuestionTarget],
) -> tuple[QuestionSection, ...]:
    """Map visible HTML sections to catalog questions without guessing."""
    if not questions:
        raise QuestionMappingError("catalog contains no questions")

    parser = _VisibleQuestionParser()
    parser.feed(html)
    parser.close()
    blocks = _split_numbered_question_blocks(parser.finish(), len(questions))

    if len(questions) == 1:
        question = questions[0]
        matching_heading = next(
            (
                index
                for index, (kind, text) in enumerate(blocks)
                if kind == "heading" and _matches_question(text, question, 0)
            ),
            None,
        )
        if matching_heading is not None:
            text = _section_text(blocks[matching_heading + 1 :])
        else:
            text = extract_html_blocks(html)
        if not text:
            raise QuestionMappingError(f"missing text for {question.id}")
        return (QuestionSection(question.id, question.label, text),)

    sections: dict[str, list[str]] = {}
    current: QuestionTarget | None = None
    for kind, text in blocks:
        matched = _find_question(text, questions) if kind == "heading" else None
        if matched is None and kind == "block" and current is None:
            matched = _find_question(text, questions)
        if matched is not None:
            if matched.id in sections:
                raise QuestionMappingError(f"duplicate section for {matched.id}")
            current = matched
            sections[matched.id] = []
            continue
        if current is not None:
            sections[current.id].append(text)

    missing = [question.id for question in questions if question.id not in sections]
    empty = [question_id for question_id, parts in sections.items() if not _section_text(parts)]
    if missing:
        raise QuestionMappingError(
            "missing question sections: " + ", ".join(missing)
        )
    if empty:
        raise QuestionMappingError(
            "empty question sections: " + ", ".join(empty)
        )

    return tuple(
        QuestionSection(
            question.id,
            question.label,
            _section_text(sections[question.id]),
        )
        for question in questions
    )


def _split_numbered_question_blocks(
    blocks: Sequence[tuple[str, str]], question_count: int
) -> list[tuple[str, str]]:
    expanded: list[tuple[str, str]] = []
    for kind, text in blocks:
        matches = list(_NUMBERED_QUESTION_PATTERN.finditer(text))
        if (
            len(matches) != question_count
            or not matches
            or matches[0].start() != 0
        ):
            expanded.append((kind, text))
            continue

        for index, match in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(text)
            )
            statement = text[match.end() : end].strip()
            if not statement:
                continue
            expanded.append(("heading", f"Question {match.group('number')}"))
            expanded.append(("block", statement))
    return expanded


def _find_question(
    text: str, questions: Sequence[QuestionTarget]
) -> QuestionTarget | None:
    matches = [
        question
        for index, question in enumerate(questions)
        if _matches_question(text, question, index)
    ]
    if len(matches) > 1:
        raise QuestionMappingError(f"ambiguous question heading: {text}")
    return matches[0] if matches else None


def _matches_question(text: str, question: QuestionTarget, index: int) -> bool:
    normalized_text = _normalize_label(text)
    if normalized_text == _normalize_label(question.label):
        return True
    if normalized_text == _normalize_label(question.id):
        return True
    match = re.fullmatch(r"(?:question|soal)\s+(\d+)", normalized_text)
    return match is not None and int(match.group(1)) == index + 1


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _section_text(parts: Sequence[str]) -> str:
    return "\n\n".join(part for part in parts if part).strip()
