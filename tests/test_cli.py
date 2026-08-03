from pathlib import Path
import shutil
import sys

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader import main
from grader_core.grading import (
    CriterionAssessment,
    EvidenceCitation,
    GradingResponse,
    VisualEvidenceItem,
    VisualEvidenceResponse,
)
from grader_core.results import ResultsDocument


VALID_RUBRIC = """\
schema_version: 1
assignment:
  id: cli-test
  title: CLI test
  total_points: 10
  feedback_language: id
  overall_feedback_below: 8
items:
  - id: question_1
    label: Question 1
    max_points: 10
    criteria:
      - id: criterion_1
        description: Explains the answer
        max_points: 10
        required_evidence: A clear explanation
        levels:
          - score: 0
            description: Missing
          - score: 5
            description: Partial
          - score: 10
            description: Complete
"""


def _write_rubric(tmp_path: Path) -> Path:
    path = tmp_path / "rubric.yaml"
    path.write_text(VALID_RUBRIC, encoding="utf-8")
    return path


def test_validate_command_accepts_valid_rubric(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rubric_path = _write_rubric(tmp_path)

    exit_code = main(["validate", "--rubric", str(rubric_path)])

    assert exit_code == 0
    assert "valid" in capsys.readouterr().out.lower()


def test_grade_dry_run_discovers_and_normalizes_without_api_client(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rubric_path = _write_rubric(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_CLI"
    student_folder = assignment_root / "123_TEST STUDENT"
    student_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
    output_path = tmp_path / "results_v2.json"

    def fail_if_initialized(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not initialize the API client")

    monkeypatch.setattr("grader.OpenRouterClient", fail_if_initialized)

    exit_code = main(
        [
            "grade",
            "--rubric",
            str(rubric_path),
            "--input",
            str(assignment_root),
            "--output",
            str(output_path),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert not output_path.exists()
    output = capsys.readouterr().out.lower()
    assert "1 submission" in output
    assert "dry run" in output


def test_grade_writes_a_versioned_result_without_network_access(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rubric_path = _write_rubric(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_CLI"
    student_folder = assignment_root / "123_TEST STUDENT"
    student_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
    (student_folder / "Question.html").write_text(
        "<p>Explain the answer.</p>", encoding="utf-8"
    )
    output_path = tmp_path / "results_v2.json"

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def extract_visual_evidence(
            self, _prompt: str, pages: list[object]
        ) -> VisualEvidenceResponse:
            return VisualEvidenceResponse(
                evidence=[
                    VisualEvidenceItem(
                        page=getattr(page, "page_number"),
                        description="Bukti terlihat jelas.",
                        readability="clear",
                    )
                    for page in pages
                ]
            )

        def request_grade(
            self, _prompt: str, _evidence: object
        ) -> GradingResponse:
            return GradingResponse(
                assessments=[
                    CriterionAssessment(
                        item_id="question_1",
                        criterion_id="criterion_1",
                        selected_score=10,
                        rationale="Jawaban didukung bukti.",
                        evidence=[EvidenceCitation(page=1, quote="Jawaban normal")],
                        readability="clear",
                    )
                ],
                item_feedback={"question_1": "Jawaban sudah baik."},
                overall_feedback="Pertahankan kualitas jawaban.",
            )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("grader.OpenRouterClient", FakeClient)

    exit_code = main(
        [
            "grade",
            "--rubric",
            str(rubric_path),
            "--input",
            str(assignment_root),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    document = ResultsDocument.model_validate_json(output_path.read_text())
    assert document.results[0].status == "graded"
    assert document.results[0].grade is not None
    assert document.results[0].grade.total_score == 10


def test_grade_rejects_legacy_output_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rubric_path = _write_rubric(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_CLI"
    assignment_root.mkdir()

    exit_code = main(
        [
            "grade",
            "--rubric",
            str(rubric_path),
            "--input",
            str(assignment_root),
            "--output",
            "individual_results.json",
            "--dry-run",
        ]
    )

    assert exit_code == 2
    assert "legacy" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("rubric_name", ["individual.yaml", "group.yaml"])
def test_project_rubrics_are_valid(rubric_name: str) -> None:
    rubric_path = PROJECT_ROOT / "rubrics" / rubric_name

    exit_code = main(["validate", "--rubric", str(rubric_path)])

    assert exit_code == 0
