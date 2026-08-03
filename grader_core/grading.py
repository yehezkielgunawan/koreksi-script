from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from grader_core.config import RubricConfig


NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class EvidenceCitation(_ResponseModel):
    page: PositiveInt
    quote: NonEmptyStr


class VisualEvidenceItem(_ResponseModel):
    page: PositiveInt
    transcription: str = ""
    description: NonEmptyStr
    readability: Literal["clear", "partial", "unreadable"]


class VisualEvidenceResponse(_ResponseModel):
    evidence: list[VisualEvidenceItem] = Field(min_length=1)
    review_reasons: list[NonEmptyStr] = Field(default_factory=list)


class CriterionAssessment(_ResponseModel):
    item_id: NonEmptyStr
    criterion_id: NonEmptyStr
    selected_score: NonNegativeInt
    rationale: NonEmptyStr
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    readability: Literal["clear", "partial", "unreadable"]


class GradingResponse(_ResponseModel):
    assessments: list[CriterionAssessment] = Field(min_length=1)
    item_feedback: dict[NonEmptyStr, NonEmptyStr]
    overall_feedback: NonEmptyStr
    review_reasons: list[NonEmptyStr] = Field(default_factory=list)


class EvidencePackage(_ResponseModel):
    question_text: str = ""
    answer_text: str = ""
    visual_evidence: list[VisualEvidenceItem] = Field(default_factory=list)


class GradingValidationError(ValueError):
    """Raised when a model response cannot be reconciled with a rubric."""


def validate_visual_evidence(
    response: VisualEvidenceResponse, page_count: int
) -> None:
    errors = [
        f"evidence references page {item.page}, but document has {page_count} pages"
        for item in response.evidence
        if item.page > page_count
    ]
    if errors:
        raise GradingValidationError("; ".join(errors))


def validate_grading_response(
    response: GradingResponse, rubric: RubricConfig, page_count: int
) -> None:
    expected = {
        (item.id, criterion.id): criterion
        for item in rubric.items
        for criterion in item.criteria
    }
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []

    for assessment in response.assessments:
        key = (assessment.item_id, assessment.criterion_id)
        if key in seen:
            errors.append(f"duplicate assessment for {key[0]}/{key[1]}")
            continue
        seen.add(key)

        criterion = expected.get(key)
        if criterion is None:
            errors.append(f"unknown assessment for {key[0]}/{key[1]}")
            continue

        allowed_scores = {level.score for level in criterion.levels}
        if assessment.selected_score not in allowed_scores:
            errors.append(
                f"score {assessment.selected_score} is not declared for "
                f"{key[0]}/{key[1]}"
            )

        if assessment.selected_score > 0 and not assessment.evidence:
            errors.append(
                f"nonzero score for {key[0]}/{key[1]} requires evidence"
            )

        for citation in assessment.evidence:
            if citation.page > page_count:
                errors.append(
                    f"{key[0]}/{key[1]} references page {citation.page}, "
                    f"but document has {page_count} pages"
                )

    missing = sorted(set(expected) - seen)
    errors.extend(
        f"missing assessment for {item_id}/{criterion_id}"
        for item_id, criterion_id in missing
    )

    if errors:
        raise GradingValidationError("; ".join(errors))


def visual_evidence_schema() -> dict:
    return VisualEvidenceResponse.model_json_schema()


def grading_response_schema() -> dict:
    return GradingResponse.model_json_schema()
