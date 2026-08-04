from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader_core.config import RubricConfig
from grader_core.grading import (
    CalculatedGrade,
    CriterionAssessment,
    EvidenceCitation,
    GradeCalculationError,
    GradingResponse,
    GradingValidationError,
    calculate_grade,
    grading_response_schema,
    render_rubric_prompt,
    validate_grading_response,
)


PROMPT_ROOT = PROJECT_ROOT / "prompts"


@pytest.fixture
def rubric() -> RubricConfig:
    return RubricConfig.model_validate(
        {
            "schema_version": 1,
            "assignment": {
                "id": "test-assignment",
                "title": "Test assignment",
                "total_points": 10,
                "feedback_language": "id",
                "overall_feedback_below": 8,
            },
            "items": [
                {
                    "id": "question_1",
                    "label": "Question 1",
                    "max_points": 10,
                    "criteria": [
                        {
                            "id": "criterion_a",
                            "description": "Explains the answer",
                            "max_points": 6,
                            "required_evidence": "A clear explanation",
                            "levels": [
                                {"score": 0, "description": "Missing"},
                                {"score": 3, "description": "Partial"},
                                {"score": 6, "description": "Complete"},
                            ],
                        },
                        {
                            "id": "criterion_b",
                            "description": "Applies the answer",
                            "max_points": 4,
                            "required_evidence": "A relevant application",
                            "levels": [
                                {"score": 0, "description": "Missing"},
                                {"score": 4, "description": "Complete"},
                            ],
                        },
                    ],
                }
            ],
        }
    )


def _assessment(
    criterion_id: str,
    score: int,
    *,
    evidence: list[EvidenceCitation] | None = None,
    rationale: str = "The answer provides relevant evidence.",
) -> CriterionAssessment:
    return CriterionAssessment(
        item_id="question_1",
        criterion_id=criterion_id,
        selected_score=score,
        rationale=rationale,
        evidence=(
            [EvidenceCitation(page=1, quote="Relevant answer")]
            if evidence is None
            else evidence
        ),
    )


def test_response_models_accept_valid_grading_data() -> None:
    grading = GradingResponse(
        assessments=[_assessment("criterion_a", 6), _assessment("criterion_b", 4)],
        item_feedback={"question_1": "Jawaban lengkap."},
        overall_feedback="Pertahankan argumentasi yang jelas.",
    )

    assert len(grading.assessments) == 2


def test_response_models_reject_unknown_properties() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EvidenceCitation(page=1, quote="Evidence", unexpected=True)


def test_validation_rejects_duplicate_assessments(rubric: RubricConfig) -> None:
    response = GradingResponse(
        assessments=[_assessment("criterion_a", 6), _assessment("criterion_a", 3)],
        item_feedback={"question_1": "Feedback"},
        overall_feedback="Overall feedback.",
    )

    with pytest.raises(GradingValidationError, match="duplicate assessment"):
        validate_grading_response(response, rubric, page_count=1)


def test_validation_rejects_missing_criteria(rubric: RubricConfig) -> None:
    response = GradingResponse(
        assessments=[_assessment("criterion_a", 6)],
        item_feedback={"question_1": "Feedback"},
        overall_feedback="Overall feedback.",
    )

    with pytest.raises(GradingValidationError, match="missing assessment"):
        validate_grading_response(response, rubric, page_count=1)


def test_validation_rejects_scores_not_declared_by_rubric(rubric: RubricConfig) -> None:
    response = GradingResponse(
        assessments=[_assessment("criterion_a", 1), _assessment("criterion_b", 4)],
        item_feedback={"question_1": "Feedback"},
        overall_feedback="Overall feedback.",
    )

    with pytest.raises(GradingValidationError, match="not declared"):
        validate_grading_response(response, rubric, page_count=1)


def test_validation_rejects_nonexistent_evidence_page(rubric: RubricConfig) -> None:
    response = GradingResponse(
        assessments=[
            _assessment(
                "criterion_a",
                6,
                evidence=[EvidenceCitation(page=2, quote="Evidence")],
            ),
            _assessment("criterion_b", 4),
        ],
        item_feedback={"question_1": "Feedback"},
        overall_feedback="Overall feedback.",
    )

    with pytest.raises(GradingValidationError, match="page 2"):
        validate_grading_response(response, rubric, page_count=1)


def test_response_model_rejects_empty_rationale() -> None:
    with pytest.raises(ValidationError, match="rationale"):
        _assessment("criterion_a", 6, rationale="   ")


def test_validation_rejects_nonzero_score_without_evidence(rubric: RubricConfig) -> None:
    response = GradingResponse(
        assessments=[
            _assessment("criterion_a", 6, evidence=[]),
            _assessment("criterion_b", 0, evidence=[]),
        ],
        item_feedback={"question_1": "Feedback"},
        overall_feedback="Overall feedback.",
    )

    with pytest.raises(GradingValidationError, match="requires evidence"):
        validate_grading_response(response, rubric, page_count=1)


def test_grading_schema_constrains_item_and_criterion_ids(
    rubric: RubricConfig,
) -> None:
    schema = grading_response_schema(rubric)
    properties = schema["$defs"]["CriterionAssessment"]["properties"]

    assert properties["item_id"]["enum"] == ["question_1"]
    assert properties["criterion_id"]["enum"] == ["criterion_a", "criterion_b"]


def test_grading_schema_constrains_scores_to_declared_levels(
    rubric: RubricConfig,
) -> None:
    schema = grading_response_schema(rubric)
    properties = schema["$defs"]["CriterionAssessment"]["properties"]

    assert properties["selected_score"]["enum"] == [0, 3, 4, 6]


def test_render_rubric_prompt_includes_rubric_criteria(
    rubric: RubricConfig,
) -> None:
    rendered = render_rubric_prompt(rubric)

    assert "Test assignment" in rendered
    assert "question_1" in rendered
    assert "Question 1" in rendered
    assert "criterion_a" in rendered
    assert "Explains the answer" in rendered
    assert "A clear explanation" in rendered
    assert "0 = Missing" in rendered
    assert "6 = Complete" in rendered


def test_prompt_file_contains_versioned_untrusted_content_rules() -> None:
    grading_prompt = (PROMPT_ROOT / "grading.md").read_text(encoding="utf-8")

    assert "prompt_version: 3" in grading_prompt
    assert "untrusted" in grading_prompt
    assert "ignore" in grading_prompt.lower()
    assert "Bahasa Indonesia" in grading_prompt
    assert "page" in grading_prompt.lower()


def _weighted_rubric() -> RubricConfig:
    items = [
        ("question_1", "Question 1", 25, "criterion_1", 25, 22),
        ("question_2", "Question 2", 35, "criterion_2", 35, 29),
        ("question_3", "Question 3", 40, "criterion_3", 40, 34),
    ]
    return RubricConfig.model_validate(
        {
            "schema_version": 1,
            "assignment": {
                "id": "weighted-assignment",
                "title": "Weighted assignment",
                "total_points": 100,
                "feedback_language": "id",
                "overall_feedback_below": 80,
            },
            "items": [
                {
                    "id": item_id,
                    "label": label,
                    "max_points": item_max,
                    "criteria": [
                        {
                            "id": criterion_id,
                            "description": label,
                            "max_points": criterion_max,
                            "required_evidence": "A page citation",
                            "levels": [
                                {"score": 0, "description": "Missing"},
                                {"score": passing_score, "description": "Good"},
                                {"score": criterion_max, "description": "Complete"},
                            ],
                        }
                    ],
                }
                for item_id, label, item_max, criterion_id, criterion_max, passing_score in items
            ],
        }
    )


def _weighted_response(
    scores: tuple[int, int, int],
) -> GradingResponse:
    return GradingResponse(
        assessments=[
            CriterionAssessment(
                item_id=f"question_{index}",
                criterion_id=f"criterion_{index}",
                selected_score=score,
                rationale="Evidence supports this criterion.",
                evidence=(
                    []
                    if score == 0
                    else [EvidenceCitation(page=index, quote="Supporting answer")]
                ),
            )
            for index, score in enumerate(scores, start=1)
        ],
        item_feedback={
            "question_1": "Feedback question 1.",
            "question_2": "Feedback question 2.",
            "question_3": "Feedback question 3.",
        },
        overall_feedback="Overall feedback.",
    )


def test_calculate_grade_uses_percentage_for_weakest_item() -> None:
    grade = calculate_grade(_weighted_response((22, 29, 34)), _weighted_rubric())

    assert isinstance(grade, CalculatedGrade)
    assert grade.criterion_scores == {
        "criterion_1": 22,
        "criterion_2": 29,
        "criterion_3": 34,
    }
    assert grade.item_scores == {"question_1": 22, "question_2": 29, "question_3": 34}
    assert grade.item_percentages == {
        "question_1": 88.0,
        "question_2": 82.85714285714286,
        "question_3": 85.0,
    }
    assert grade.total_score == 85
    assert grade.weakest_item_id == "question_2"
    assert grade.feedback == "Feedback question 2."


def test_calculate_grade_uses_overall_feedback_below_threshold() -> None:
    grade = calculate_grade(_weighted_response((0, 29, 34)), _weighted_rubric())

    assert grade.total_score == 63
    assert grade.feedback == "Overall feedback."


def test_calculate_grade_rejects_missing_item_feedback() -> None:
    response = _weighted_response((22, 29, 34))
    response.item_feedback.pop("question_2")

    with pytest.raises(GradeCalculationError, match="question_2"):
        calculate_grade(response, _weighted_rubric())
