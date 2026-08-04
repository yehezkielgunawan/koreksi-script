from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader_core.results import (
    RESULT_SCHEMA_VERSION,
    ResultFingerprint,
    ResultGrade,
    ResultRecord,
    ResultStatus,
    ResultsDocument,
    ResultStore,
    build_fingerprint,
)


def _fingerprint(*, source_sha256: str = "source") -> ResultFingerprint:
    return build_fingerprint(
        source_sha256=source_sha256,
        normalized_pdf_sha256="pdf",
        question_sha256="question",
        rubric_sha256="rubric",
        grading_prompt_version="grading-3",
        model_id="google/gemma-4-26b-a4b-it:free",
        extractor_version="pdf-upload-1",
    )


def _grade() -> ResultGrade:
    return ResultGrade(
        criterion_scores={"criterion_1": 8},
        item_scores={"question_1": 8},
        item_percentages={"question_1": 80.0},
        total_score=8,
        weakest_item_id="question_1",
        feedback="Perbaiki bukti pendukung.",
        review_reasons=[],
    )


def _record(
    *,
    file_path: str = "StudentAnswer/student/answer.pdf",
    status: ResultStatus = "graded",
    fingerprint: ResultFingerprint | None = None,
) -> ResultRecord:
    return ResultRecord(
        status=status,
        student_id="123",
        student_name="Student Name",
        file_path=file_path,
        fingerprint=fingerprint or _fingerprint(),
        grade=_grade() if status == "graded" else None,
        error=None,
    )


def test_fingerprint_is_stable_and_changes_when_any_input_changes() -> None:
    first = _fingerprint()
    second = _fingerprint()
    changed = _fingerprint(source_sha256="changed-source")

    assert first == second
    assert first.digest != ""
    assert first.digest != changed.digest
    assert first.result_schema_version == RESULT_SCHEMA_VERSION


def test_result_record_requires_grade_for_graded_status() -> None:
    with pytest.raises(ValidationError, match="grade"):
        ResultRecord(
            status="graded",
            student_id="123",
            student_name="Student Name",
            file_path="answer.pdf",
            fingerprint=_fingerprint(),
            grade=None,
            error=None,
        )


def test_result_record_requires_error_for_error_status() -> None:
    with pytest.raises(ValidationError, match="error"):
        ResultRecord(
            status="error",
            student_id="123",
            student_name="Student Name",
            file_path="answer.pdf",
            fingerprint=_fingerprint(),
            grade=None,
            error=None,
        )


def test_results_document_uses_version_four() -> None:
    document = ResultsDocument(results=[_record()])

    assert document.schema_version == RESULT_SCHEMA_VERSION
    assert document.results[0].status == "graded"


def test_results_document_rejects_version_two() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        ResultsDocument.model_validate({"schema_version": 2, "results": []})


def test_fingerprint_rejects_legacy_visual_prompt_field() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResultFingerprint.model_validate(
            {
                "source_sha256": "source",
                "normalized_pdf_sha256": "pdf",
                "question_sha256": "question",
                "rubric_sha256": "rubric",
                "visual_prompt_version": "visual-1",
                "grading_prompt_version": "grading-3",
                "model_id": "google/gemma-4-26b-a4b-it:free",
                "extractor_version": "pdf-upload-1",
                "result_schema_version": 4,
                "digest": "digest",
            }
        )


def test_result_store_saves_atomically_and_loads_exact_cache(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "results_v3.json"
    review_path = tmp_path / "review_queue.json"
    store = ResultStore(output_path, review_path)
    record = _record()

    store.save_result(record)

    loaded = store.load()
    assert loaded.results == [record]
    assert store.find_cached(record.file_path, record.fingerprint) == record
    assert store.find_cached(record.file_path, _fingerprint(source_sha256="new")) is None
    assert not list(tmp_path.glob(".results_v3.json.*.tmp"))


def test_result_store_replaces_same_file_and_keeps_review_queue_separate(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "results_v3.json"
    review_path = tmp_path / "review_queue.json"
    store = ResultStore(output_path, review_path)
    first = _record()
    review = _record(
        status="needs_review",
        fingerprint=_fingerprint(source_sha256="review-source"),
    )

    store.save_result(first)
    store.save_result(review)

    assert store.load().results == [review]
    assert ResultsDocument.model_validate_json(review_path.read_text()).results == [review]


def test_result_store_preserves_existing_file_if_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "results_v3.json"
    store = ResultStore(output_path)
    first = _record()
    store.save_result(first)
    original = output_path.read_text(encoding="utf-8")

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("grader_core.results.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.save_result(_record(fingerprint=_fingerprint(source_sha256="new")))

    assert output_path.read_text(encoding="utf-8") == original
