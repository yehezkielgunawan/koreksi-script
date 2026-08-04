from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RESULT_SCHEMA_VERSION = 3
EXTRACTOR_VERSION = "extractor-1"
ResultStatus = Literal["graded", "needs_review", "error"]

NonEmptyStr = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
Percentage = Annotated[float, Field(ge=0, le=100)]
Score100 = Annotated[int, Field(ge=0, le=100)]


class _ResultModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class ResultFingerprint(_ResultModel):
    source_sha256: NonEmptyStr
    normalized_pdf_sha256: NonEmptyStr
    question_sha256: NonEmptyStr
    rubric_sha256: NonEmptyStr
    visual_prompt_version: NonEmptyStr
    grading_prompt_version: NonEmptyStr
    model_id: NonEmptyStr
    extractor_version: NonEmptyStr
    result_schema_version: Literal[3]
    digest: NonEmptyStr

    @field_validator("result_schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != RESULT_SCHEMA_VERSION:
            raise ValueError("result_schema_version must be integer 3")
        return value


class ResultGrade(_ResultModel):
    criterion_scores: dict[NonEmptyStr, NonNegativeInt]
    item_scores: dict[NonEmptyStr, NonNegativeInt]
    item_percentages: dict[NonEmptyStr, Percentage]
    total_score: Score100
    weakest_item_id: NonEmptyStr
    feedback: NonEmptyStr
    review_reasons: list[NonEmptyStr] = Field(default_factory=list)


class ResultRecord(_ResultModel):
    schema_version: Literal[3] = RESULT_SCHEMA_VERSION
    status: ResultStatus
    student_id: NonEmptyStr
    student_name: NonEmptyStr
    file_path: NonEmptyStr
    fingerprint: ResultFingerprint
    grade: ResultGrade | None = None
    error: NonEmptyStr | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != RESULT_SCHEMA_VERSION:
            raise ValueError("schema_version must be integer 3")
        return value

    @model_validator(mode="after")
    def validate_status_payload(self) -> "ResultRecord":
        if self.status == "graded" and self.grade is None:
            raise ValueError("graded result requires grade")
        if self.status == "error" and self.error is None:
            raise ValueError("error result requires error")
        if self.status == "error" and self.grade is not None:
            raise ValueError("error result cannot contain grade")
        return self


class ResultsDocument(_ResultModel):
    schema_version: Literal[3] = RESULT_SCHEMA_VERSION
    results: list[ResultRecord] = Field(default_factory=list)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != RESULT_SCHEMA_VERSION:
            raise ValueError("schema_version must be integer 3")
        return value


def build_fingerprint(
    *,
    source_sha256: str,
    normalized_pdf_sha256: str,
    question_sha256: str,
    rubric_sha256: str,
    visual_prompt_version: str,
    grading_prompt_version: str,
    model_id: str,
    extractor_version: str = EXTRACTOR_VERSION,
) -> ResultFingerprint:
    parts = {
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "question_sha256": question_sha256,
        "rubric_sha256": rubric_sha256,
        "visual_prompt_version": visual_prompt_version,
        "grading_prompt_version": grading_prompt_version,
        "model_id": model_id,
        "extractor_version": extractor_version,
        "result_schema_version": RESULT_SCHEMA_VERSION,
    }
    canonical = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ResultFingerprint(
        **parts,
        digest=sha256(canonical).hexdigest(),
    )


class ResultStore:
    """Atomically persists versioned results and a separate review queue."""

    def __init__(self, output_path: Path, review_queue_path: Path | None = None) -> None:
        self.output_path = Path(output_path)
        self.review_queue_path = review_queue_path or self.output_path.with_name(
            f"{self.output_path.stem}_review.json"
        )

    def load(self) -> ResultsDocument:
        if not self.output_path.exists():
            return ResultsDocument()
        return ResultsDocument.model_validate_json(
            self.output_path.read_text(encoding="utf-8")
        )

    def find_cached(
        self, file_path: str, fingerprint: ResultFingerprint
    ) -> ResultRecord | None:
        for record in self.load().results:
            if (
                record.status == "graded"
                and record.file_path == file_path
                and record.fingerprint == fingerprint
            ):
                return record
        return None

    def save_result(self, record: ResultRecord) -> ResultsDocument:
        document = self.load()
        results = [
            existing
            for existing in document.results
            if existing.file_path != record.file_path
        ]
        results.append(record)
        updated = ResultsDocument(results=results)
        _atomic_write(self.output_path, updated)

        review_results = [item for item in results if item.status == "needs_review"]
        if review_results:
            _atomic_write(
                self.review_queue_path,
                ResultsDocument(results=review_results),
            )
        elif self.review_queue_path.exists():
            _atomic_write(self.review_queue_path, ResultsDocument())
        return updated


def _atomic_write(path: Path, document: ResultsDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        file_descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                document.model_dump(mode="json"),
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
