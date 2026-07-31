from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader_core.documents import (
    AMBIGUOUS_ANSWER_FILES,
    MISSING_ANSWER_FILE,
    SubmissionFiles,
    discover_submissions,
    extract_html_blocks,
)


def _write(path: Path, content: str = "sample") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_discover_submissions_excludes_question_and_declaration_files(
    tmp_path: Path,
) -> None:
    assignment_root = tmp_path / "StudentAnswer_ISYS6599"
    student_folder = assignment_root / "2902737810_LUK SEKAR DADARI"
    answer_path = _write(student_folder / "answer.pdf")
    _write(student_folder / "Question.pdf")
    _write(student_folder / "ai_usage_declaration.pdf")
    question_path = _write(student_folder / "Question.html", "<p>Question text</p>")

    submissions = discover_submissions([assignment_root])

    assert submissions == [
        SubmissionFiles(
            student_id="2902737810",
            student_name="LUK SEKAR DADARI",
            folder=student_folder,
            answer_path=answer_path,
            question_path=question_path,
            review_reasons=(),
        )
    ]


def test_discover_submissions_flags_multiple_answer_candidates(tmp_path: Path) -> None:
    assignment_root = tmp_path / "StudentAnswer_ISYS6599"
    student_folder = assignment_root / "2902737810_LUK SEKAR DADARI"
    _write(student_folder / "answer.pdf")
    _write(student_folder / "revised_answer.docx")

    submission = discover_submissions([assignment_root])[0]

    assert submission.answer_path is None
    assert submission.review_reasons == (AMBIGUOUS_ANSWER_FILES,)


def test_discover_submissions_flags_missing_answer_file(tmp_path: Path) -> None:
    assignment_root = tmp_path / "StudentAnswer_ISYS6599"
    student_folder = assignment_root / "2902737810_LUK SEKAR DADARI"
    _write(student_folder / "Question.pdf")

    submission = discover_submissions([assignment_root])[0]

    assert submission.answer_path is None
    assert submission.review_reasons == (MISSING_ANSWER_FILE,)


def test_discover_submissions_rejects_non_assignment_roots(tmp_path: Path) -> None:
    non_assignment_root = tmp_path / "uploads"
    non_assignment_root.mkdir()

    submissions = discover_submissions([non_assignment_root])

    assert submissions == []


def test_extract_html_blocks_preserves_numbered_questions() -> None:
    html = """
    <h2>Question 1</h2><p>Explain COBIT.</p>
    <ol><li>Define the framework.</li><li>Give an example.</li></ol>
    <table><tr><th>Criterion</th><td>Evidence</td></tr></table>
    <p>Question 2<br>Apply ISO 31000.</p>
    """

    assert extract_html_blocks(html) == (
        "Question 1\n\n"
        "Explain COBIT.\n\n"
        "Define the framework.\n"
        "Give an example.\n\n"
        "Criterion\n"
        "Evidence\n\n"
        "Question 2\n"
        "Apply ISO 31000."
    )
