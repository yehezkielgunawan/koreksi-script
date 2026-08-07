from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from time import monotonic

from dotenv import load_dotenv

from grader_core import (
    DEFAULT_MODEL,
    AssignmentSelector,
    NormalizationFailure,
    QuestionMappingError,
    QuestionSection,
    ResultGrade,
    ResultRecord,
    ResultStore,
    SubmissionFiles,
    build_fingerprint,
    calculate_grade,
    discover_submissions,
    extract_question_sections,
    load_assignment_selector,
    load_rubric_catalog,
    select_catalog_assignment,
    normalize_document,
    validate_grading_response,
)
from grader_core.config import RubricCatalog, RubricConfig, RubricLoadError
from grader_core.documents import NormalizedDocument
from grader_core.grading import grading_response_schema, render_rubric_prompt
from grader_core.openrouter import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    OpenRouterClient,
    format_openrouter_error,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_GRADING_PROMPT = PROJECT_ROOT / "prompts" / "grading.md"
DEFAULT_OUTPUT = "results_v4.json"
LEGACY_OUTPUT_NAMES = {"individual_results.json", "group_results.json", "results_v3.json"}
PROMPT_VERSION_PATTERN = re.compile(r"prompt_version:\s*([^\s>-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class SubmissionContext:
    submission: SubmissionFiles
    selector: AssignmentSelector
    selector_path: Path | None
    rubric: RubricConfig


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _validate_rubric(Path(args.rubric))

    try:
        catalog, contexts = _preflight(args)
    except (OSError, RubricLoadError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.command == "regrade":
        contexts = [
            context
            for context in contexts
            if context.submission.student_id == args.student_id
        ]
        if not contexts:
            print(
                f"Error: student ID not found: {args.student_id}",
                file=sys.stderr,
            )
            return 2

    if args.dry_run:
        return _run_dry_run(args, catalog, contexts)

    try:
        return _run_grading(args, catalog, contexts)
    except (OSError, RubricLoadError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grade individual or group student submissions."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a rubric YAML file")
    validate.add_argument("--rubric", required=True)

    grade = commands.add_parser("grade", help="grade all discovered submissions")
    _add_grading_arguments(grade)
    grade.add_argument(
        "--dry-run",
        action="store_true",
        help="run discovery and document preflight without calling the API",
    )

    regrade = commands.add_parser(
        "regrade", help="force-grade one student and bypass the result cache"
    )
    _add_grading_arguments(regrade)
    regrade.add_argument("--student-id", required=True)
    regrade.add_argument("--dry-run", action="store_true")

    return parser


def _add_grading_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--input", required=True, dest="input_root")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="maximum seconds allowed for each model request",
    )
    parser.add_argument("--grading-prompt", default=str(DEFAULT_GRADING_PROMPT))


def _validate_rubric(path: Path) -> int:
    try:
        catalog = load_rubric_catalog(path)
    except (OSError, RubricLoadError, ValueError) as exc:
        print(f"Invalid rubric: {exc}", file=sys.stderr)
        return 2

    print(
        f"Rubric valid: {catalog.rubric.id} "
        f"({len(catalog.assignments)} assignment(s), 100-point entries)"
    )
    return 0


def _preflight(
    args: argparse.Namespace,
) -> tuple[RubricCatalog, list[SubmissionContext]]:
    output_path = Path(args.output)
    if output_path.name in LEGACY_OUTPUT_NAMES:
        raise ValueError(
            f"legacy output path is not allowed: {output_path.name}; "
            "use results_v4.json or another versioned output"
        )

    catalog = load_rubric_catalog(Path(args.rubric))
    input_root = Path(args.input_root)
    assignment_roots = _assignment_roots(input_root)
    submissions = discover_submissions(assignment_roots)
    if not submissions:
        raise ValueError(f"no student submissions found under {input_root}")

    _check_output_writable(output_path, create_parent=not args.dry_run)

    if not str(args.model).strip():
        raise ValueError("model must be explicit and non-empty")
    if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
        raise ValueError("request_timeout_seconds must be a finite positive number")
    if str(args.model).endswith(":free"):
        print(
            "Warning: the selected free model endpoint may be rate-limited "
            "and has provider privacy tradeoffs.",
            file=sys.stderr,
        )

    _check_prompt(Path(args.grading_prompt), "grading prompt")

    selector_cache: dict[Path, AssignmentSelector] = {}
    implicit_selector: AssignmentSelector | None = None
    contexts: list[SubmissionContext] = []
    for submission in submissions:
        selector_path = _selector_path(submission)
        if selector_path is None:
            if len(catalog.assignments) != 1:
                raise RubricLoadError(
                    "assignment selector not found for "
                    f"{submission.folder}; expected assignment.yaml in the student "
                    "folder or StudentAnswer* root"
                )
            if implicit_selector is None:
                implicit_selector = AssignmentSelector.model_validate(
                    {
                        "schema_version": 1,
                        "assignment": {"id": catalog.assignments[0].id},
                    }
                )
                print(
                    "Warning: assignment.yaml not found; using the sole catalog "
                    f"assignment {implicit_selector.assignment.id!r}.",
                    file=sys.stderr,
                )
            selector = implicit_selector
        else:
            selector = selector_cache.get(selector_path)
            if selector is None:
                selector = load_assignment_selector(selector_path)
                selector_cache[selector_path] = selector
        contexts.append(
            SubmissionContext(
                submission=submission,
                selector=selector,
                selector_path=selector_path,
                rubric=select_catalog_assignment(catalog, selector.assignment.id),
            )
        )

    document_extensions = {
        submission.answer_path.suffix.casefold()
        for submission in submissions
        if submission.answer_path is not None
    }
    if document_extensions & {".doc", ".docx"} and shutil.which("libreoffice") is None:
        if args.dry_run:
            print(
                "Warning: LibreOffice is unavailable; DOC/DOCX submissions "
                "will require review.",
                file=sys.stderr,
            )
        else:
            raise ValueError(
                "LibreOffice is required to convert DOC/DOCX submissions"
            )

    if not args.dry_run and not os.getenv("OPENROUTER_API_KEY"):
        raise ValueError("OPENROUTER_API_KEY is required for grading")

    return catalog, contexts


def _selector_path(submission: SubmissionFiles) -> Path | None:
    for path in (
        submission.folder / "assignment.yaml",
        submission.assignment_root / "assignment.yaml",
    ):
        if path.is_file():
            return path
    return None


def _assignment_roots(input_root: Path) -> list[Path]:
    if not input_root.is_dir():
        raise ValueError(f"input root is not a directory: {input_root}")
    if input_root.name.startswith("StudentAnswer"):
        return [input_root]

    roots = sorted(
        path
        for path in input_root.iterdir()
        if path.is_dir() and path.name.startswith("StudentAnswer")
    )
    if not roots:
        raise ValueError(
            f"input root contains no directories starting with StudentAnswer: {input_root}"
        )
    return roots


def _check_output_writable(path: Path, *, create_parent: bool) -> None:
    if path.exists() and not path.is_file():
        raise ValueError(f"output path is not a file: {path}")
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    if not path.parent.is_dir() or not os.access(path.parent, os.W_OK):
        raise ValueError(f"output directory is not writable: {path.parent}")
    if path.exists() and not os.access(path, os.W_OK):
        raise ValueError(f"output file is not writable: {path}")


def _check_prompt(path: Path, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    with path.open("r", encoding="utf-8"):
        pass


def _run_dry_run(
    args: argparse.Namespace,
    catalog: RubricCatalog,
    contexts: Sequence[SubmissionContext],
) -> int:
    normalized_count = 0
    review_count = sum(
        bool(context.submission.review_reasons) for context in contexts
    )

    with tempfile.TemporaryDirectory(prefix="koreksi-dry-run-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        for index, context in enumerate(contexts):
            submission = context.submission
            if submission.answer_path is None:
                print(
                    f"Review: {submission.student_id or submission.folder.name} "
                    f"({', '.join(submission.review_reasons)})"
                )
                continue

            try:
                _question_sections(context)
            except QuestionMappingError as exc:
                review_count += 1
                print(
                    f"Review: {submission.student_id or submission.folder.name} "
                    f"(question mapping failed: {exc})"
                )

            student_temp = temporary_root / str(index)
            normalized = normalize_document(submission.answer_path, student_temp)
            if isinstance(normalized, NormalizationFailure):
                review_count += 1
                print(
                    f"Review: {submission.student_id or submission.folder.name} "
                    f"({normalized.review_reason})"
                )
                continue

            normalized_count += 1

    print(
        f"Dry run: {len(contexts)} submission(s), "
        f"{normalized_count} normalized, {review_count} review item(s)."
    )
    print(
        f"Rubric: {catalog.rubric.id}; no API requests were made and "
        f"no result file was written."
    )
    return 0


def _run_grading(
    args: argparse.Namespace,
    catalog: RubricCatalog,
    contexts: Sequence[SubmissionContext],
) -> int:
    output_path = Path(args.output)
    store = ResultStore(output_path)
    grading_prompt_path = Path(args.grading_prompt)
    grading_prompt = grading_prompt_path.read_text(encoding="utf-8")
    grading_prompt_version = _prompt_version(grading_prompt)
    client = OpenRouterClient(
        model=args.model,
        request_timeout_seconds=args.request_timeout,
    )
    force = args.command == "regrade"
    graded_count = 0
    cached_count = 0
    review_count = 0

    for index, context in enumerate(contexts):
        submission = context.submission
        source_path = submission.answer_path or submission.folder
        display_name = (
            submission.student_name or submission.student_id or submission.folder.name
        )

        def report_progress(message: str) -> None:
            _print_progress(index + 1, len(contexts), display_name, message)

        file_path = str(source_path)
        rubric_sha256 = _selected_rubric_sha256(
            context.rubric, context.selector.assignment.id
        )
        fingerprint = _build_submission_fingerprint(
            submission,
            normalized=None,
            rubric_sha256=rubric_sha256,
            grading_prompt_version=grading_prompt_version,
            model_id=args.model,
        )

        if submission.answer_path is not None:
            try:
                report_progress("normalizing document")
                with tempfile.TemporaryDirectory(prefix=f"koreksi-{index}-") as temp:
                    normalized = normalize_document(submission.answer_path, Path(temp))
                    if isinstance(normalized, NormalizationFailure):
                        record = _review_record(
                            submission,
                            fingerprint,
                            normalized.review_reason,
                        )
                    else:
                        fingerprint = _build_submission_fingerprint(
                            submission,
                            normalized=normalized,
                            rubric_sha256=rubric_sha256,
                            grading_prompt_version=grading_prompt_version,
                            model_id=args.model,
                        )
                        if not force and store.find_cached(file_path, fingerprint):
                            cached_count += 1
                            print(f"Cached: {submission.student_id or file_path}")
                            continue
                        try:
                            question_sections = _question_sections(context)
                        except QuestionMappingError as exc:
                            record = _review_record(
                                submission,
                                fingerprint,
                                f"question_mapping_failed:{exc}",
                            )
                        else:
                            record = _grade_submission(
                                submission,
                                normalized,
                                fingerprint,
                                context.rubric,
                                question_sections,
                                grading_prompt,
                                client,
                                report_progress,
                            )
            except Exception as exc:
                record = ResultRecord(
                    status="error",
                    student_id=submission.student_id or submission.folder.name,
                    student_name=submission.student_name or submission.folder.name,
                    file_path=file_path,
                    fingerprint=fingerprint,
                    error=_error_text(exc),
                )
        else:
            record = _review_record(
                submission,
                fingerprint,
                ", ".join(submission.review_reasons) or "missing_answer_file",
            )

        store.save_result(record)
        if record.status == "needs_review":
            review_count += 1
        elif record.status == "graded":
            graded_count += 1
        print(f"{record.status.title()}: {record.student_id}")

    print(
        f"Completed: {graded_count} graded, {review_count} review item(s), "
        f"{cached_count} cached. Results: {output_path}"
    )
    return 0


def _grade_submission(
    submission: SubmissionFiles,
    normalized: NormalizedDocument,
    fingerprint: object,
    rubric: RubricConfig,
    question_sections: Sequence[QuestionSection],
    grading_prompt: str,
    client: OpenRouterClient,
    report_progress: Callable[[str], None],
) -> ResultRecord:
    question_text = "\n\n".join(
        f"[{section.question_id}] {section.label}\n{section.text}"
        for section in question_sections
    )
    review_reasons: list[str] = []

    report_progress("requesting final grade")
    request_started = monotonic()
    response = client.request_grade(
        f"{grading_prompt}\n\n{render_rubric_prompt(rubric)}",
        question_text,
        normalized.pdf_path,
        response_schema=grading_response_schema(rubric),
        validator=lambda result: validate_grading_response(
            result, rubric, normalized.page_count
        ),
    )
    report_progress(f"final grade completed in {monotonic() - request_started:.1f}s")
    validate_grading_response(response, rubric, normalized.page_count)
    calculated = calculate_grade(response, rubric)
    review_reasons.extend(calculated.review_reasons)
    result_grade = ResultGrade(
        criterion_scores=calculated.criterion_scores,
        item_scores=calculated.item_scores,
        item_percentages=calculated.item_percentages,
        total_score=calculated.total_score,
        weakest_item_id=calculated.weakest_item_id,
        feedback=calculated.feedback,
        review_reasons=_unique_strings(review_reasons),
    )
    return ResultRecord(
        status="needs_review" if result_grade.review_reasons else "graded",
        student_id=submission.student_id or submission.folder.name,
        student_name=submission.student_name or submission.folder.name,
        file_path=str(submission.answer_path),
        fingerprint=fingerprint,
        grade=result_grade,
    )


def _question_sections(context: SubmissionContext) -> tuple[QuestionSection, ...]:
    submission = context.submission
    if submission.question_path is None:
        raise QuestionMappingError("missing_question_file")
    try:
        return extract_question_sections(
            submission.question_path.read_text(encoding="utf-8"),
            context.rubric.items,
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise QuestionMappingError(
            f"question_read_failed:{type(exc).__name__}"
        ) from exc


def _review_record(
    submission: SubmissionFiles,
    fingerprint: object,
    reason: str,
) -> ResultRecord:
    source_path = submission.answer_path or submission.folder
    return ResultRecord(
        status="needs_review",
        student_id=submission.student_id or submission.folder.name,
        student_name=submission.student_name or submission.folder.name,
        file_path=str(source_path),
        fingerprint=fingerprint,
        grade=ResultGrade(
            criterion_scores={},
            item_scores={},
            item_percentages={},
            total_score=0,
            weakest_item_id="review_required",
            feedback="Diperlukan pemeriksaan manual.",
            review_reasons=[reason],
        ),
    )


def _build_submission_fingerprint(
    submission: SubmissionFiles,
    *,
    normalized: NormalizedDocument | None,
    rubric_sha256: str,
    grading_prompt_version: str,
    model_id: str,
) -> object:
    if normalized is not None:
        source_sha256 = normalized.source_sha256
        normalized_pdf_sha256 = normalized.pdf_sha256
    elif submission.answer_path is not None:
        source_sha256 = _file_sha256(submission.answer_path)
        normalized_pdf_sha256 = source_sha256
    else:
        source_sha256 = _text_sha256(str(submission.folder))
        normalized_pdf_sha256 = source_sha256

    question_sha256 = (
        _file_sha256(submission.question_path)
        if submission.question_path is not None
        else _text_sha256("")
    )
    return build_fingerprint(
        source_sha256=source_sha256,
        normalized_pdf_sha256=normalized_pdf_sha256,
        question_sha256=question_sha256,
        rubric_sha256=rubric_sha256,
        grading_prompt_version=grading_prompt_version,
        model_id=model_id,
    )


def _prompt_version(prompt: str) -> str:
    match = PROMPT_VERSION_PATTERN.search(prompt)
    return match.group(1) if match else _text_sha256(prompt)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_rubric_sha256(rubric: RubricConfig, assignment_id: str) -> str:
    canonical = json.dumps(
        {
            "assignment_id": assignment_id,
            "rubric": rubric.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _text_sha256(canonical)


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _unique_strings(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _print_progress(
    index: int,
    total: int,
    display_name: str,
    message: str,
) -> None:
    print(f"[{index}/{total}] {display_name}: {message}", flush=True)


def _error_text(error: Exception) -> str:
    return format_openrouter_error(error)


if __name__ == "__main__":
    raise SystemExit(main())
