from copy import deepcopy
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader_core.config import (
    AssignmentManifest,
    RubricConfig,
    RubricLoadError,
    RubricTemplate,
    build_effective_rubric,
    load_rubric,
)


VALID_RUBRIC_YAML = """\
schema_version: 1
assignment:
  id: assignment-1
  title: Individual Assignment 1
  total_points: 10
  feedback_language: id
  overall_feedback_below: 8
items:
  - id: item-1
    label: Question 1
    max_points: 10
    criteria:
      - id: criterion-1
        description: Explains the core concept
        max_points: 10
        required_evidence: A definition and example
        levels:
          - score: 0
            description: No relevant answer
          - score: 10
            description: Complete explanation
"""


@pytest.fixture
def valid_rubric_data() -> dict:
    return {
        "schema_version": 1,
        "assignment": {
            "id": "assignment-1",
            "title": "Individual Assignment 1",
            "total_points": 10,
            "feedback_language": "id",
            "overall_feedback_below": 8,
        },
        "items": [
            {
                "id": "item-1",
                "label": "Question 1",
                "max_points": 10,
                "criteria": [
                    {
                        "id": "criterion-1",
                        "description": "Explains the core concept",
                        "max_points": 6,
                        "required_evidence": "A definition and example",
                        "levels": [
                            {"score": 0, "description": "No relevant answer"},
                            {"score": 3, "description": "Partial explanation"},
                            {"score": 6, "description": "Complete explanation"},
                        ],
                    },
                    {
                        "id": "criterion-2",
                        "description": "Applies the concept",
                        "max_points": 4,
                        "required_evidence": "A justified application",
                        "levels": [
                            {"score": 0, "description": "No application"},
                            {"score": 4, "description": "Correct application"},
                        ],
                    },
                ],
            }
        ],
    }


def test_valid_rubric_model_loads(valid_rubric_data: dict) -> None:
    rubric = RubricConfig.model_validate(valid_rubric_data)

    assert rubric.schema_version == 1
    assert rubric.assignment.feedback_language == "id"
    assert rubric.items[0].criteria[0].levels[-1].score == 6


@pytest.mark.parametrize(
    "schema_version",
    [True, 1.0, "1", 2],
    ids=["boolean", "float", "string", "unsupported-integer"],
)
def test_schema_version_rejects_values_other_than_integer_one(
    valid_rubric_data: dict, schema_version: object
) -> None:
    data = deepcopy(valid_rubric_data)
    data["schema_version"] = schema_version

    with pytest.raises(ValidationError, match="schema_version"):
        RubricConfig.model_validate(data)


def test_schema_version_accepts_integer_one(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["schema_version"] = 1

    rubric = RubricConfig.model_validate(data)

    assert rubric.schema_version == 1
    assert type(rubric.schema_version) is int


def test_valid_yaml_file_loads(
    tmp_path: Path, valid_rubric_data: dict
) -> None:
    rubric_path = tmp_path / "rubric.yaml"
    rubric_path.write_text(VALID_RUBRIC_YAML, encoding="utf-8")

    rubric = load_rubric(rubric_path)

    assert rubric.assignment.id == valid_rubric_data["assignment"]["id"]
    assert rubric.assignment.total_points == 10


def test_item_maximum_mismatch_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["items"][0]["max_points"] = 9

    with pytest.raises(ValidationError, match="criterion max_points sum"):
        RubricConfig.model_validate(data)


def test_assignment_total_mismatch_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["assignment"]["total_points"] = 11

    with pytest.raises(ValidationError, match="item max_points sum"):
        RubricConfig.model_validate(data)


def test_duplicate_item_ids_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["items"].append(deepcopy(data["items"][0]))
    data["assignment"]["total_points"] = 20

    with pytest.raises(ValidationError, match="duplicate item id: item-1"):
        RubricConfig.model_validate(data)


def test_duplicate_criterion_ids_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    second_item = deepcopy(data["items"][0])
    second_item["id"] = "item-2"
    second_item["criteria"][0]["id"] = "criterion-2"
    data["items"].append(second_item)
    data["assignment"]["total_points"] = 20

    with pytest.raises(ValidationError, match="duplicate criterion id: criterion-2"):
        RubricConfig.model_validate(data)


def test_duplicate_level_score_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["items"][0]["criteria"][0]["levels"].append(
        {"score": 3, "description": "Another partial level"}
    )

    with pytest.raises(ValidationError, match="duplicate level score: 3"):
        RubricConfig.model_validate(data)


def test_missing_zero_level_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["items"][0]["criteria"][0]["levels"] = [
        level
        for level in data["items"][0]["criteria"][0]["levels"]
        if level["score"] != 0
    ]

    with pytest.raises(ValidationError, match="must include score 0"):
        RubricConfig.model_validate(data)


def test_missing_max_level_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["items"][0]["criteria"][0]["levels"] = [
        level
        for level in data["items"][0]["criteria"][0]["levels"]
        if level["score"] != 6
    ]

    with pytest.raises(ValidationError, match="must include max_points score 6"):
        RubricConfig.model_validate(data)


@pytest.mark.parametrize("score", [-1, 7])
def test_out_of_range_level_rejected(
    valid_rubric_data: dict, score: int
) -> None:
    data = deepcopy(valid_rubric_data)
    data["items"][0]["criteria"][0]["levels"].append(
        {"score": score, "description": "Out of range"}
    )

    with pytest.raises(ValidationError, match="between 0 and max_points 6"):
        RubricConfig.model_validate(data)


@pytest.mark.parametrize("threshold", [-1, 11])
def test_threshold_outside_total_rejected(
    valid_rubric_data: dict, threshold: int
) -> None:
    data = deepcopy(valid_rubric_data)
    data["assignment"]["overall_feedback_below"] = threshold

    with pytest.raises(ValidationError, match="overall_feedback_below"):
        RubricConfig.model_validate(data)


def test_unknown_root_property_rejected(valid_rubric_data: dict) -> None:
    data = deepcopy(valid_rubric_data)
    data["unexpected"] = True

    with pytest.raises(ValidationError) as exc_info:
        RubricConfig.model_validate(data)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"
    assert exc_info.value.errors()[0]["loc"] == ("unexpected",)


@pytest.mark.parametrize(
    ("path", "unknown_key", "value"),
    [
        (("assignment",), "unexpected_assignment", True),
        (("items", 0), "unexpected_item", True),
        (("items", 0, "criteria", 0), "unexpected_criterion", True),
        (
            ("items", 0, "criteria", 0, "levels", 0),
            "unexpected_level",
            True,
        ),
    ],
)
def test_unknown_nested_properties_rejected(
    valid_rubric_data: dict,
    path: tuple[str | int, ...],
    unknown_key: str,
    value: object,
) -> None:
    data = deepcopy(valid_rubric_data)
    target = data
    for part in path:
        target = target[part]
    target[unknown_key] = value

    with pytest.raises(ValidationError) as exc_info:
        RubricConfig.model_validate(data)

    assert any(
        error["type"] == "extra_forbidden" and error["loc"][-1] == unknown_key
        for error in exc_info.value.errors()
    )


@pytest.mark.parametrize(
    "path",
    [
        ("assignment", "title"),
        ("items", 0, "label"),
        ("items", 0, "criteria", 0, "required_evidence"),
        ("items", 0, "criteria", 0, "levels", 0, "description"),
    ],
)
def test_whitespace_only_required_strings_rejected(
    valid_rubric_data: dict, path: tuple[str | int, ...]
) -> None:
    data = deepcopy(valid_rubric_data)
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = "   "

    with pytest.raises(ValidationError):
        RubricConfig.model_validate(data)


@pytest.mark.parametrize(
    "path",
    [
        ("items",),
        ("items", 0, "criteria"),
        ("items", 0, "criteria", 0, "levels"),
    ],
)
def test_required_collections_reject_empty_lists(
    valid_rubric_data: dict, path: tuple[str | int, ...]
) -> None:
    data = deepcopy(valid_rubric_data)
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = []

    with pytest.raises(ValidationError):
        RubricConfig.model_validate(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("assignment", "total_points"), "10"),
        (("items", 0, "max_points"), 10.0),
        (("items", 0, "criteria", 0, "levels", 0, "score"), True),
    ],
)
def test_representative_fields_reject_coerced_types(
    valid_rubric_data: dict,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    data = deepcopy(valid_rubric_data)
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        RubricConfig.model_validate(data)


def test_empty_yaml_rejected_clearly(tmp_path: Path) -> None:
    rubric_path = tmp_path / "empty.yaml"
    rubric_path.write_text("", encoding="utf-8")

    with pytest.raises(RubricLoadError) as exc_info:
        load_rubric(rubric_path)

    message = str(exc_info.value)
    assert str(rubric_path) in message
    assert "empty" in message.lower()


def test_malformed_yaml_rejected_clearly(tmp_path: Path) -> None:
    rubric_path = tmp_path / "malformed.yaml"
    rubric_path.write_text("items: [", encoding="utf-8")

    with pytest.raises(RubricLoadError) as exc_info:
        load_rubric(rubric_path)

    message = str(exc_info.value)
    assert str(rubric_path) in message
    assert "yaml" in message.lower()


def test_invalid_utf8_rejected_as_rubric_load_error(tmp_path: Path) -> None:
    rubric_path = tmp_path / "invalid-utf8.yaml"
    rubric_path.write_bytes(b"\xff\xfe")

    with pytest.raises(RubricLoadError) as exc_info:
        load_rubric(rubric_path)

    assert str(rubric_path) in str(exc_info.value)


@pytest.mark.parametrize("location", ["root", "nested"])
def test_duplicate_yaml_keys_rejected(
    tmp_path: Path, location: str
) -> None:
    rubric_path = tmp_path / f"duplicate-{location}.yaml"
    if location == "root":
        content = VALID_RUBRIC_YAML.replace(
            "schema_version: 1",
            "schema_version: 1\nschema_version: 1",
            1,
        )
    else:
        content = VALID_RUBRIC_YAML.replace(
            "  id: assignment-1",
            "  id: assignment-1\n  id: duplicate-assignment",
            1,
        )
    rubric_path.write_text(content, encoding="utf-8")

    with pytest.raises(RubricLoadError) as exc_info:
        load_rubric(rubric_path)

    message = str(exc_info.value)
    assert str(rubric_path) in message
    assert "duplicate" in message.lower()


def test_missing_rubric_file_rejected_clearly(tmp_path: Path) -> None:
    rubric_path = tmp_path / "missing.yaml"

    with pytest.raises(RubricLoadError) as exc_info:
        load_rubric(rubric_path)

    assert str(rubric_path) in str(exc_info.value)


def _template_data() -> dict:
    return {
        "schema_version": 2,
        "rubric": {
            "id": "individual",
            "feedback_language": "id",
            "overall_feedback_below": 80,
        },
        "criteria": [
            {
                "id": "understanding",
                "weight": 0.5,
                "description": "Understands the question",
                "required_evidence": "A direct answer",
                "levels": [
                    {"fraction": 0.0, "description": "Missing"},
                    {"fraction": 0.5, "description": "Partial"},
                    {"fraction": 1.0, "description": "Complete"},
                ],
            },
            {
                "id": "analysis",
                "weight": 0.5,
                "description": "Analyzes the problem",
                "required_evidence": "Relevant reasoning",
                "levels": [
                    {"fraction": 0.0, "description": "Missing"},
                    {"fraction": 0.5, "description": "Partial"},
                    {"fraction": 1.0, "description": "Complete"},
                ],
            },
        ],
    }


def _manifest_data() -> dict:
    return {
        "schema_version": 1,
        "assignment": {
            "id": "week-04",
            "title": "Week 4",
            "total_points": 100,
        },
        "questions": [
            {"id": "question_1", "label": "Question 1", "max_points": 40},
            {"id": "question_2", "label": "Question 2", "max_points": 60},
        ],
    }


def test_assignment_manifest_requires_exactly_100_points() -> None:
    manifest = AssignmentManifest.model_validate(_manifest_data())

    assert manifest.assignment.total_points == 100
    assert sum(question.max_points for question in manifest.questions) == 100


def test_assignment_manifest_rejects_non_100_question_total() -> None:
    data = _manifest_data()
    data["questions"][1]["max_points"] = 50

    with pytest.raises(ValidationError, match="sum 90"):
        AssignmentManifest.model_validate(data)


def test_template_weights_must_sum_to_one() -> None:
    data = _template_data()
    data["criteria"][1]["weight"] = 0.4

    with pytest.raises(ValidationError, match="weights must sum to 1"):
        RubricTemplate.model_validate(data)


def test_effective_rubric_generates_dynamic_question_items() -> None:
    template = RubricTemplate.model_validate(_template_data())
    manifest = AssignmentManifest.model_validate(_manifest_data())

    rubric = build_effective_rubric(template, manifest)

    assert rubric.assignment.total_points == 100
    assert [item.id for item in rubric.items] == ["question_1", "question_2"]
    assert [item.max_points for item in rubric.items] == [40, 60]
    assert all(
        sum(criterion.max_points for criterion in item.criteria) == item.max_points
        for item in rubric.items
    )
    assert rubric.items[0].criteria[0].id == "question_1__understanding"


def test_effective_rubric_supports_five_questions() -> None:
    template = RubricTemplate.model_validate(_template_data())
    data = _manifest_data()
    data["questions"] = [
        {
            "id": f"question_{index}",
            "label": f"Question {index}",
            "max_points": 20,
        }
        for index in range(1, 6)
    ]
    manifest = AssignmentManifest.model_validate(data)

    rubric = build_effective_rubric(template, manifest)

    assert len(rubric.items) == 5
    assert sum(item.max_points for item in rubric.items) == 100
