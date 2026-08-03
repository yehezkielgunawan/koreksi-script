from dataclasses import dataclass
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


class GradeCalculationError(ValueError):
    """Raised when a validated response cannot produce a complete grade."""


@dataclass(frozen=True)
class CalculatedGrade:
    criterion_scores: dict[str, int]
    item_scores: dict[str, int]
    item_percentages: dict[str, float]
    total_score: int
    weakest_item_id: str
    feedback: str
    review_reasons: tuple[str, ...]


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
    response: GradingResponse, rubric: RubricConfig, page_count: int | None = None
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
            if page_count is not None and citation.page > page_count:
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


def calculate_grade(
    response: GradingResponse, rubric: RubricConfig
) -> CalculatedGrade:
    """Calculate totals and feedback selection without trusting model arithmetic."""
    validate_grading_response(response, rubric)

    assessments = {
        (assessment.item_id, assessment.criterion_id): assessment
        for assessment in response.assessments
    }
    criterion_scores = {
        criterion.id: assessments[(item.id, criterion.id)].selected_score
        for item in rubric.items
        for criterion in item.criteria
    }
    item_scores = {
        item.id: sum(
            assessments[(item.id, criterion.id)].selected_score
            for criterion in item.criteria
        )
        for item in rubric.items
    }
    item_percentages = {
        item.id: item_scores[item.id] / item.max_points * 100
        for item in rubric.items
    }

    missing_feedback = [
        item.id for item in rubric.items if item.id not in response.item_feedback
    ]
    if missing_feedback:
        raise GradeCalculationError(
            "missing item feedback for: " + ", ".join(missing_feedback)
        )

    weakest_item_id = min(item_percentages, key=item_percentages.__getitem__)
    total_score = sum(item_scores.values())
    if total_score >= rubric.assignment.overall_feedback_below:
        feedback = response.item_feedback[weakest_item_id]
    else:
        feedback = response.overall_feedback

    review_reasons = list(response.review_reasons)
    for item in rubric.items:
        for criterion in item.criteria:
            assessment = assessments[(item.id, criterion.id)]
            if assessment.readability == "unreadable":
                reason = f"unreadable_evidence:{item.id}/{criterion.id}"
                if reason not in review_reasons:
                    review_reasons.append(reason)

    return CalculatedGrade(
        criterion_scores=criterion_scores,
        item_scores=item_scores,
        item_percentages=item_percentages,
        total_score=total_score,
        weakest_item_id=weakest_item_id,
        feedback=feedback,
        review_reasons=tuple(review_reasons),
    )


def visual_evidence_schema() -> dict:
    return VisualEvidenceResponse.model_json_schema()


def grading_response_schema() -> dict:
    return GradingResponse.model_json_schema()
