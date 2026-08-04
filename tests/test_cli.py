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
        criteria: Explains the answer.
"""

MULTIPLE_ASSIGNMENTS_RUBRIC = VALID_RUBRIC + """\
  - id: week-02
    title: Week 2
    total_points: 100
    questions:
      - id: question_1
        label: Question 1
        max_points: 100
        criteria: Explains the answer.
"""

VALID_SELECTOR = """\
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


def _write_selector(assignment_root: Path, content: str = VALID_SELECTOR) -> Path:
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
    _write_selector(assignment_root)
    output_path = tmp_path / "results_v4.json"
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


def test_grade_dry_run_uses_sole_catalog_assignment_without_selector(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rubric_path = _write_catalog(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_GROUP_ASSIGNMENT"
    group_folder = assignment_root / "Group_Group-1"
    group_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], group_folder / "answer.pdf")
    (group_folder / "Question.html").write_text(
        "<p>Question 1</p>", encoding="utf-8"
    )

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
            str(tmp_path / "results_v4.json"),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert "1 submission" in capsys.readouterr().out


def test_grade_writes_a_versioned_result_without_network_access(
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
    _write_selector(assignment_root)
    (student_folder / "Question.html").write_text(
        "<p>Explain the answer.</p>", encoding="utf-8"
    )
    output_path = tmp_path / "results_v4.json"
    client_kwargs: dict[str, object] = {}
    grade_prompts: list[str] = []
    grade_pdf_paths: list[object] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            client_kwargs.update(kwargs)

        def request_grade(
            self, prompt: str, _question_text: str, pdf_path: object, **_kwargs: object
        ) -> GradingResponse:
            grade_prompts.append(prompt)
            grade_pdf_paths.append(pdf_path)
            return GradingResponse(
                assessments=[
                    CriterionAssessment(
                        item_id="question_1",
                        criterion_id="question_1_criterion",
                        selected_score=100,
                        rationale="Jawaban didukung bukti.",
                        evidence=[EvidenceCitation(page=1, quote="Jawaban normal")],
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
            "--request-timeout",
            "90",
        ]
    )

    assert exit_code == 0
    assert client_kwargs["request_timeout_seconds"] == 90
    assert "Explains the answer." in grade_prompts[0]
    assert "question_1_criterion" in grade_prompts[0]
    assert Path(grade_pdf_paths[0]).is_file()
    document = ResultsDocument.model_validate_json(output_path.read_text())
    assert document.results[0].status == "graded"
    assert document.results[0].grade is not None
    assert document.results[0].grade.total_score == 100
    assert all(0 <= value <= 100 for value in document.results[0].grade.item_percentages.values())
    output = capsys.readouterr().out
    assert "[1/1] TEST STUDENT: normalizing document" in output
    assert "[1/1] TEST STUDENT: requesting final grade" in output


@pytest.mark.parametrize("request_timeout", ["0", "-1"])
def test_grade_rejects_non_positive_request_timeout(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request_timeout: str,
) -> None:
    rubric_path = _write_catalog(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_TIMEOUT"
    student_folder = assignment_root / "123_TEST STUDENT"
    student_folder.mkdir(parents=True)
    shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
    _write_selector(assignment_root)
    (student_folder / "Question.html").write_text(
        "<p>Explain the answer.</p>", encoding="utf-8"
    )
    monkeypatch.setattr(
        "grader.OpenRouterClient",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid timeout must fail in preflight"
        ),
    )

    exit_code = main(
        [
            "grade",
            "--rubric",
            str(rubric_path),
            "--input",
            str(assignment_root),
            "--output",
            str(tmp_path / "results_v4.json"),
            "--request-timeout",
            request_timeout,
        ]
    )

    assert exit_code == 2
    assert "request_timeout_seconds" in capsys.readouterr().err


def test_grade_persists_timeout_error_and_continues_to_next_submission(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rubric_path = _write_catalog(tmp_path)
    assignment_root = tmp_path / "StudentAnswer_TIMEOUT_CONTINUE"
    for student_folder_name in ("123_FIRST STUDENT", "456_SECOND STUDENT"):
        student_folder = assignment_root / student_folder_name
        student_folder.mkdir(parents=True)
        shutil.copyfile(synthetic_pdf_files["text"], student_folder / "answer.pdf")
        (student_folder / "Question.html").write_text(
            "<p>Explain the answer.</p>", encoding="utf-8"
        )
    _write_selector(assignment_root)
    output_path = tmp_path / "results_v4.json"
    grade_calls = 0

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def request_grade(
            self, _prompt: str, _question_text: str, _pdf_path: object, **_kwargs: object
        ) -> GradingResponse:
            nonlocal grade_calls
            grade_calls += 1
            if grade_calls == 1:
                raise TimeoutError("request timed out")
            return GradingResponse(
                assessments=[
                    CriterionAssessment(
                        item_id="question_1",
                        criterion_id="question_1_criterion",
                        selected_score=100,
                        rationale="Jawaban didukung bukti.",
                        evidence=[EvidenceCitation(page=1, quote="Jawaban normal")],
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
    records = {record.student_id: record for record in document.results}
    assert records["123"].status == "error"
    assert records["123"].error == "request timed out"
    assert records["456"].status == "graded"
    output = capsys.readouterr().out
    assert "[1/2] FIRST STUDENT: requesting final grade" in output
    assert "[2/2] SECOND STUDENT: requesting final grade" in output


def test_grade_dry_run_uses_dynamic_catalog_questions(
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
    _write_selector(
        assignment_root,
        VALID_SELECTOR.replace("week-01", "week-dynamic"),
    )
    (assignment_root / "Question.html").write_text(
        "<h2>Question 1</h2><p>First statement.</p>"
        "<h2>Question 2</h2><p>Second statement.</p>",
        encoding="utf-8",
    )
    output_path = tmp_path / "results_v4.json"
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


def test_grade_requires_assignment_selector_before_api(
    tmp_path: Path,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rubric_path = _write_catalog(tmp_path, MULTIPLE_ASSIGNMENTS_RUBRIC)
    assignment_root = tmp_path / "StudentAnswer_NO_SELECTOR"
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
            str(tmp_path / "results_v4.json"),
        ]
    )

    assert exit_code == 2
    assert "assignment selector" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "output_name",
    ["individual_results.json", "group_results.json", "results_v3.json"],
)
def test_grade_rejects_legacy_output_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], output_name: str
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
            output_name,
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
