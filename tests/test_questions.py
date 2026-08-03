from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader_core.questions import QuestionMappingError, extract_question_sections


def _questions() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(id="question_1", label="Question 1", max_points=40),
        SimpleNamespace(id="question_2", label="Question 2", max_points=60),
    ]


def test_extract_question_sections_maps_catalog_questions() -> None:
    html = """
    <h2>Question 1</h2><p>Explain governance.</p>
    <h2>Question 2</h2><p>Apply the framework.</p>
    """

    sections = extract_question_sections(html, _questions())

    assert [(section.question_id, section.text) for section in sections] == [
        ("question_1", "Explain governance."),
        ("question_2", "Apply the framework."),
    ]


def test_extract_question_sections_accepts_all_text_for_one_question() -> None:
    question = [
        SimpleNamespace(id="question_1", label="Question 1", max_points=100)
    ]

    sections = extract_question_sections("<p>Explain governance.</p>", question)

    assert sections[0].text == "Explain governance."


def test_extract_question_sections_rejects_ambiguous_multi_question_html() -> None:
    with pytest.raises(QuestionMappingError, match="question_2"):
        extract_question_sections("<p>Unlabeled question text.</p>", _questions())
