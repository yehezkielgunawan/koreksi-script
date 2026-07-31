from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable

import pdfplumber
import pypdf


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
DOCUMENT_CONVERSION_UNAVAILABLE = "document_conversion_unavailable"
DOCUMENT_CONVERSION_FAILED = "document_conversion_failed"

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


@dataclass(frozen=True)
class PageContent:
    number: int
    text: str
    image_hashes: tuple[str, ...]
    has_table: bool
    has_drawings: bool


@dataclass(frozen=True)
class NormalizedDocument:
    source_path: Path
    pdf_path: Path
    source_sha256: str
    pdf_sha256: str
    pages: tuple[PageContent, ...]


@dataclass(frozen=True)
class NormalizationFailure:
    source_path: Path
    review_reason: str


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


def normalize_document(
    source_path: Path, temporary_dir: Path
) -> NormalizedDocument | NormalizationFailure:
    """Normalize a supported submission to PDF and collect page-level diagnostics."""
    source_suffix = source_path.suffix.casefold()
    if source_suffix == ".pdf":
        pdf_path = source_path
    elif source_suffix in {".doc", ".docx"}:
        conversion_result = _convert_to_pdf(source_path, temporary_dir)
        if isinstance(conversion_result, NormalizationFailure):
            return conversion_result
        pdf_path = conversion_result
    else:
        raise ValueError(f"Unsupported document type: {source_path.suffix}")

    return NormalizedDocument(
        source_path=source_path,
        pdf_path=pdf_path,
        source_sha256=_file_sha256(source_path),
        pdf_sha256=_file_sha256(pdf_path),
        pages=_page_content(pdf_path),
    )


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


def _convert_to_pdf(source_path: Path, temporary_dir: Path) -> Path | NormalizationFailure:
    libreoffice_path = shutil.which("libreoffice")
    if libreoffice_path is None:
        return NormalizationFailure(source_path, DOCUMENT_CONVERSION_UNAVAILABLE)

    temporary_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                libreoffice_path,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(temporary_dir),
                str(source_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return NormalizationFailure(source_path, DOCUMENT_CONVERSION_FAILED)

    output_path = temporary_dir / f"{source_path.stem}.pdf"
    if result.returncode != 0 or not output_path.is_file():
        return NormalizationFailure(source_path, DOCUMENT_CONVERSION_FAILED)
    return output_path


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _page_content(pdf_path: Path) -> tuple[PageContent, ...]:
    reader = pypdf.PdfReader(pdf_path)
    with pdfplumber.open(pdf_path) as plumber_pdf:
        if len(reader.pages) != len(plumber_pdf.pages):
            raise ValueError(f"PDF page count mismatch while reading {pdf_path}")

        return tuple(
            PageContent(
                number=index,
                text=(reader_page.extract_text() or "").strip(),
                image_hashes=_image_hashes(reader_page),
                has_table=bool(plumber_page.find_tables()),
                has_drawings=bool(
                    plumber_page.lines or plumber_page.rects or plumber_page.curves
                ),
            )
            for index, (reader_page, plumber_page) in enumerate(
                zip(reader.pages, plumber_pdf.pages, strict=True), start=1
            )
        )


def _image_hashes(page: pypdf.PageObject) -> tuple[str, ...]:
    resources = page.get("/Resources")
    if resources is None:
        return ()
    return tuple(_image_hashes_from_resources(resources.get_object(), set()))


def _image_hashes_from_resources(resources: object, seen: set[int]) -> list[str]:
    if not isinstance(resources, dict):
        return []
    xobjects = resources.get("/XObject")
    if xobjects is None:
        return []

    hashes: list[str] = []
    for reference in xobjects.get_object().values():
        stream = reference.get_object()
        stream_id = id(stream)
        if stream_id in seen:
            continue
        seen.add(stream_id)

        subtype = stream.get("/Subtype")
        if subtype == "/Image":
            raw_data = getattr(stream, "_data", None)
            # pypdf does not expose raw stream bytes publicly for every stream type.
            # Falling back to get_data still avoids PIL/image rendering.
            data = raw_data if raw_data is not None else stream.get_data()
            hashes.append(sha256(data).hexdigest())
        elif subtype == "/Form":
            nested_resources = stream.get("/Resources")
            if nested_resources is not None:
                hashes.extend(
                    _image_hashes_from_resources(nested_resources.get_object(), seen)
                )
    return hashes
