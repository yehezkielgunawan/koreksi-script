from dataclasses import dataclass
from collections import Counter
from hashlib import sha256
from html.parser import HTMLParser
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable

from PIL import Image
import pdfplumber
import pypdf
import pypdfium2 as pdfium


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
    drawing_count: int = 0


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


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    path: Path
    width: int
    height: int


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


def select_visual_pages(
    document: NormalizedDocument,
    *,
    min_text_chars: int = 200,
    max_drawing_count: int = 10,
    template_fraction: float = 0.5,
) -> tuple[int, ...]:
    """Select pages whose layout or visual content may affect grading."""
    if min_text_chars < 0:
        raise ValueError("min_text_chars must be non-negative")
    if max_drawing_count < 0:
        raise ValueError("max_drawing_count must be non-negative")
    if not 0 < template_fraction <= 1:
        raise ValueError("template_fraction must be greater than 0 and at most 1")

    page_count = len(document.pages)
    repeated_threshold = math.ceil(page_count * template_fraction)
    image_occurrences = Counter(
        image_hash
        for page in document.pages
        for image_hash in page.image_hashes
    )
    repeated_templates = {
        image_hash
        for image_hash, count in image_occurrences.items()
        if count >= repeated_threshold
    }

    selected: list[int] = []
    for page in document.pages:
        text_length = len(" ".join(page.text.split()))
        has_unique_image = any(
            image_hash not in repeated_templates for image_hash in page.image_hashes
        )
        if (
            text_length < min_text_chars
            or page.has_table
            or page.drawing_count > max_drawing_count
            or has_unique_image
        ):
            selected.append(page.number)
    return tuple(selected)


def chunk_visual_pages(
    page_numbers: Iterable[int], *, max_images: int = 8
) -> tuple[tuple[int, ...], ...]:
    """Split 1-indexed page numbers into bounded image-request chunks."""
    if max_images <= 0:
        raise ValueError("max_images must be positive")
    pages = tuple(page_numbers)
    if any(page_number < 1 for page_number in pages):
        raise ValueError("page numbers must be positive and 1-indexed")
    return tuple(
        pages[start : start + max_images]
        for start in range(0, len(pages), max_images)
    )


def render_visual_pages(
    pdf_path: Path,
    page_numbers: Iterable[int],
    output_dir: Path,
    *,
    scale: float = 2.0,
    max_pixels: int = 2_000_000,
) -> tuple[RenderedPage, ...]:
    """Render selected 1-indexed PDF pages as bounded RGB PNG files."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    if max_pixels <= 0:
        raise ValueError("max_pixels must be positive")

    pages = tuple(page_numbers)
    if any(page_number < 1 for page_number in pages):
        raise ValueError("page numbers must be positive and 1-indexed")

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered_pages: list[RenderedPage] = []
    with pdfium.PdfDocument(pdf_path) as pdf:
        page_count = len(pdf)
        if any(page_number > page_count for page_number in pages):
            raise ValueError(f"page number exceeds PDF page count {page_count}")

        for page_number in pages:
            page = pdf[page_number - 1]
            try:
                page_width, page_height = page.get_size()
                requested_pixels = (
                    page_width * scale * page_height * scale
                )
                effective_scale = scale
                if requested_pixels > max_pixels:
                    effective_scale *= math.sqrt(max_pixels / requested_pixels)

                bitmap = page.render(
                    scale=effective_scale,
                    fill_color=(255, 255, 255, 255),
                    maybe_alpha=False,
                )
                try:
                    image = bitmap.to_pil().convert("RGB")
                    if image.width * image.height > max_pixels:
                        resize_scale = math.sqrt(
                            max_pixels / (image.width * image.height)
                        )
                        image = image.resize(
                            (
                                max(1, math.floor(image.width * resize_scale)),
                                max(1, math.floor(image.height * resize_scale)),
                            ),
                            Image.Resampling.LANCZOS,
                        )
                    output_path = output_dir / f"page-{page_number:04d}.png"
                    image.save(output_path, format="PNG", optimize=True)
                    rendered_pages.append(
                        RenderedPage(
                            page_number=page_number,
                            path=output_path,
                            width=image.width,
                            height=image.height,
                        )
                    )
                finally:
                    bitmap.close()
            finally:
                page.close()
    return tuple(rendered_pages)


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
                has_drawings=(drawing_count := _drawing_count(plumber_page)) > 0,
                drawing_count=drawing_count,
            )
            for index, (reader_page, plumber_page) in enumerate(
                zip(reader.pages, plumber_pdf.pages, strict=True), start=1
            )
        )


def _drawing_count(page: object) -> int:
    return sum(
        len(getattr(page, attribute, ()) or ())
        for attribute in ("lines", "rects", "curves")
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
