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
schema_version: 3
rubric:
  id: cli-test
  feedback_language: id
  overall_feedback_below: 80
assignments:
  - id: week-01
    title: Week 1
    total_points: 100
    questions:
      - id: question_1
        label: Question 1
        max_points: 100
        criteria:
          - id: criterion_1
            description: Explains the answer
            max_points: 100
            required_evidence: A clear explanation
            levels:
              - score: 0
                description: Missing
              - score: 50
                description: Partial
              - score: 100
                description: Complete
"""

VALID_MANIFEST = """\
schema_version: 1
assignment:
  id: week-01
"""

DYNAMIC_RUBRIC = """\
schema_version: 3
rubric:
  id: cli-dynamic
  feedback_language: id
  overall_feedback_below: 80
assignments:
  - id: week-dynamic
    title: Dynamic Week
    total_points: 100
    questions:
      - id: question_1
        label: Question 1
        max_points: 40
        criteria:
          - id: first_specific_criterion
            description: First question criterion
            max_points: 40
            required_evidence: First evidence
            levels:
              - score: 0
                description: Missing
              - score: 40
                description: Complete
      - id: question_2
        label: Question 2
        max_points: 60
        criteria:
          - id: second_specific_criterion
            description: Second question criterion
            max_points: 60
            required_evidence: Second evidence
            levels:
              - score: 0
                description: Missing
              - score: 60
                description: Complete
"""


def _write_catalog(tmp_path: Path, content: str = VALID_RUBRIC) -> Path:
    path = tmp_path / "rubric.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _write_manifest(assignment_root: Path, content: str = VALID_MANIFEST) -> Path:
    path = assignment_root / "assignment.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_validate_command_accepts_valid_rubric(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rubric_path = _write_catalog(tmp_path)

    exit_code = main(["validate", "--rubric", str(rubric_path)])

    assert exit_code == 0
    assert "valid" in capsys.readouterr().out.lower()


def test_grade_dry_run_discovers_and_normalizes_without_api_client(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rubric_path = _write_catalog(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_CLI"
    student_folder = assignment_root / "123_TEST STUDENT"
    student_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
    _write_manifest(assignment_root)
    output_path = tmp_path / "results_v3.json"

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
    rubric_path = _write_catalog(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_CLI"
    student_folder = assignment_root / "123_TEST STUDENT"
    student_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
    _write_manifest(assignment_root)
    (student_folder / "Question.html").write_text(
        "<p>Explain the answer.</p>", encoding="utf-8"
    )
    output_path = tmp_path / "results_v3.json"

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
                        selected_score=100,
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
    assert document.results[0].grade.total_score == 100
    assert all(0 <= value <= 100 for value in document.results[0].grade.item_percentages.values())


def test_grade_dry_run_uses_dynamic_manifest_questions(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rubric_path = _write_catalog(tmp_path, DYNAMIC_RUBRIC)
    assignment_root = tmp_path / "StudentAnswer_DYNAMIC"
    student_folder = assignment_root / "123_TEST STUDENT"
    student_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
    _write_manifest(
        assignment_root,
        VALID_MANIFEST.replace("week-01", "week-dynamic"),
    )
    (assignment_root / "Question.html").write_text(
        "<h2>Question 1</h2><p>First statement.</p>"
        "<h2>Question 2</h2><p>Second statement.</p>",
        encoding="utf-8",
    )
    output_path = tmp_path / "results_v3.json"

    monkeypatch.setattr(
        "grader.OpenRouterClient",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not initialize API"),
    )

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
    assert "dry run" in capsys.readouterr().out.lower()


def test_grade_requires_assignment_manifest_before_api(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rubric_path = _write_catalog(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_NO_MANIFEST"
    student_folder = assignment_root / "123_TEST STUDENT"
    student_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
    monkeypatch.setattr(
        "grader.OpenRouterClient",
        lambda *_args, **_kwargs: pytest.fail("API must not initialize"),
    )

    exit_code = main(
        [
            "grade",
            "--rubric",
            str(rubric_path),
            "--input",
            str(assignment_root),
            "--output",
            str(tmp_path / "results_v3.json"),
        ]
    )

    assert exit_code == 2
    assert "assignment selector" in capsys.readouterr().err.lower()


def test_grade_rejects_legacy_output_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rubric_path = _write_catalog(tmp_path)
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
