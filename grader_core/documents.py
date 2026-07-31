from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Iterable


STUDENT_ANSWER_PREFIX = "StudentAnswer"
SUPPORTED_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".doc", ".docx"})
NON_ANSWER_NAME_FRAGMENTS = frozenset(
    {
        "question",
        "soal",
        "pertanyaan",
        "attachment",
        "ai_usage",
        "ai form",
        "declaration",
    }
)
MISSING_ANSWER_FILE = "missing_answer_file"
AMBIGUOUS_ANSWER_FILES = "ambiguous_answer_files"

_STUDENT_FOLDER_PATTERN = re.compile(r"^(?P<id>\d+)_(?P<name>.+)$")
_TEXT_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "blockquote",
        "dd",
        "div",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "main",
        "p",
        "section",
    }
)
_TABLE_CELL_TAGS = frozenset({"td", "th"})
_LIST_ITEM_TAGS = frozenset({"li"})


@dataclass(frozen=True)
class SubmissionFiles:
    student_id: str
    student_name: str
    folder: Path
    answer_path: Path | None
    question_path: Path | None
    review_reasons: tuple[str, ...]


class _BlockTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[tuple[str, str]] = []
        self._current_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if normalized_tag == "br":
            self._current_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in {"script", "style"}:
            self._ignored_depth = max(self._ignored_depth - 1, 0)
            return
        if self._ignored_depth:
            return
        if normalized_tag in _TEXT_BLOCK_TAGS:
            self._flush("block")
        elif normalized_tag in _LIST_ITEM_TAGS:
            self._flush("list")
        elif normalized_tag in _TABLE_CELL_TAGS:
            self._flush("cell")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._current_parts.append(data)

    def text(self) -> str:
        self._flush("block")
        result = ""
        previous_kind: str | None = None
        for kind, block in self._blocks:
            if result:
                result += (
                    "\n"
                    if kind == previous_kind and kind in {"cell", "list"}
                    else "\n\n"
                )
            result += block
            previous_kind = kind
        return result

    def _flush(self, kind: str) -> None:
        raw_text = "".join(self._current_parts)
        self._current_parts.clear()
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
        text = "\n".join(line for line in lines if line)
        if text:
            self._blocks.append((kind, text))


def extract_html_blocks(html: str) -> str:
    """Return visible HTML text with paragraph, list, and table boundaries intact."""
    extractor = _BlockTextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text()


def discover_submissions(assignment_roots: Iterable[Path]) -> list[SubmissionFiles]:
    """Discover one answer document per student without guessing ambiguous files."""
    submissions: list[SubmissionFiles] = []
    roots = sorted({root for root in assignment_roots if _is_assignment_root(root)})

    for assignment_root in roots:
        for student_folder in sorted(
            path for path in assignment_root.iterdir() if path.is_dir()
        ):
            student_id, student_name = _student_identity(student_folder.name)
            answer_candidates, question_path = _student_documents(student_folder)
            review_reasons: tuple[str, ...]
            answer_path: Path | None

            if not answer_candidates:
                answer_path = None
                review_reasons = (MISSING_ANSWER_FILE,)
            elif len(answer_candidates) > 1:
                answer_path = None
                review_reasons = (AMBIGUOUS_ANSWER_FILES,)
            else:
                answer_path = answer_candidates[0]
                review_reasons = ()

            submissions.append(
                SubmissionFiles(
                    student_id=student_id,
                    student_name=student_name,
                    folder=student_folder,
                    answer_path=answer_path,
                    question_path=question_path,
                    review_reasons=review_reasons,
                )
            )

    return submissions


def _is_assignment_root(path: Path) -> bool:
    return path.is_dir() and path.name.startswith(STUDENT_ANSWER_PREFIX)


def _student_identity(folder_name: str) -> tuple[str, str]:
    match = _STUDENT_FOLDER_PATTERN.match(folder_name)
    if match:
        return match.group("id"), match.group("name").replace("_", " ")
    return "", folder_name.replace("_", " ")


def _student_documents(student_folder: Path) -> tuple[list[Path], Path | None]:
    answer_candidates: list[Path] = []
    question_paths: list[Path] = []

    for path in sorted(candidate for candidate in student_folder.rglob("*") if candidate.is_file()):
        if path.name.casefold() == "question.html":
            question_paths.append(path)
            continue
        if path.suffix.casefold() not in SUPPORTED_DOCUMENT_EXTENSIONS:
            continue
        if any(fragment in path.name.casefold() for fragment in NON_ANSWER_NAME_FRAGMENTS):
            continue
        answer_candidates.append(path)

    return answer_candidates, question_paths[0] if question_paths else None
