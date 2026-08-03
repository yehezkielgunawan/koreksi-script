from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader_core.config import RubricConfig
from grader_core.grading import (
    CriterionAssessment,
    EvidenceCitation,
    GradingResponse,
    GradingValidationError,
    VisualEvidenceItem,
    VisualEvidenceResponse,
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
        readability="clear",
    )


def test_response_models_accept_valid_visual_and_grading_data() -> None:
    visual = VisualEvidenceResponse(
        evidence=[
            VisualEvidenceItem(
                page=2,
                transcription="A diagram label",
                description="A diagram connects the two controls.",
                readability="clear",
            )
        ]
    )
    grading = GradingResponse(
        assessments=[_assessment("criterion_a", 6), _assessment("criterion_b", 4)],
        item_feedback={"question_1": "Jawaban lengkap."},
        overall_feedback="Pertahankan argumentasi yang jelas.",
    )

    assert visual.evidence[0].page == 2
    assert len(grading.assessments) == 2


def test_response_models_reject_unknown_properties() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EvidenceCitation(page=1, quote="Evidence", unexpected=True)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        VisualEvidenceItem(
            page=1,
            transcription="Text",
            description="Description",
            readability="clear",
            unexpected=True,
        )


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


def test_visual_evidence_response_rejects_invalid_readability() -> None:
    with pytest.raises(ValidationError, match="readability"):
        VisualEvidenceItem(
            page=1,
            transcription="Text",
            description="Description",
            readability="unknown",
        )


def test_prompt_files_contain_versioned_untrusted_content_rules() -> None:
    visual_prompt = (PROMPT_ROOT / "visual_evidence.md").read_text(encoding="utf-8")
    grading_prompt = (PROMPT_ROOT / "grading.md").read_text(encoding="utf-8")

    assert "prompt_version: 1" in visual_prompt
    assert "Do not grade" in visual_prompt
    assert "untrusted" in visual_prompt
    assert "unreadable" in visual_prompt
    assert "diagram" in visual_prompt

    assert "prompt_version: 1" in grading_prompt
    assert "untrusted" in grading_prompt
    assert "ignore" in grading_prompt.lower()
    assert "Bahasa Indonesia" in grading_prompt
    assert "page" in grading_prompt.lower()
