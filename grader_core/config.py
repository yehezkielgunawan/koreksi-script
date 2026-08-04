from collections.abc import Hashable
from pathlib import Path
from typing import Annotated, Literal, Self, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
ModelT = TypeVar("ModelT", bound=BaseModel)


class _ConfigModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class ScoreLevel(_ConfigModel):
    score: int
    description: NonEmptyStr


class CriterionConfig(_ConfigModel):
    id: NonEmptyStr
    description: NonEmptyStr
    max_points: PositiveInt
    required_evidence: NonEmptyStr
    levels: list[ScoreLevel] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_levels(self) -> Self:
        scores = [level.score for level in self.levels]
        seen: set[int] = set()
        for score in scores:
            if score in seen:
                raise ValueError(f"duplicate level score: {score}")
            seen.add(score)

        for score in scores:
            if not 0 <= score <= self.max_points:
                raise ValueError(
                    f"level score {score} must be between 0 and "
                    f"max_points {self.max_points}"
                )

        if 0 not in seen:
            raise ValueError("levels must include score 0")
        if self.max_points not in seen:
            raise ValueError(
                f"levels must include max_points score {self.max_points}"
            )
        return self


class ItemConfig(_ConfigModel):
    id: NonEmptyStr
    label: NonEmptyStr
    max_points: PositiveInt
    criteria: list[CriterionConfig] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def expand_criteria_shorthand(cls, value: object) -> object:
        if not isinstance(value, dict) or not isinstance(
            value.get("criteria"), str
        ):
            return value

        item_id = value.get("id")
        max_points = value.get("max_points")
        if not isinstance(item_id, str) or not item_id.strip():
            return value
        if type(max_points) is not int or max_points <= 0:
            return value

        midpoint = max_points // 2
        levels = [
            {"score": 0, "description": "Not demonstrated."},
        ]
        if 0 < midpoint < max_points:
            levels.append(
                {
                    "score": midpoint,
                    "description": "Partially demonstrated.",
                }
            )
        levels.append(
            {"score": max_points, "description": "Fully demonstrated."}
        )

        expanded = dict(value)
        expanded["criteria"] = [
            {
                "id": f"{item_id.strip()}_criterion",
                "description": value["criteria"],
                "max_points": max_points,
                "required_evidence": (
                    "Evidence in the submission demonstrating the stated criterion."
                ),
                "levels": levels,
            }
        ]
        return expanded

    @model_validator(mode="after")
    def validate_max_points(self) -> Self:
        criteria_total = sum(criterion.max_points for criterion in self.criteria)
        if criteria_total != self.max_points:
            raise ValueError(
                f"criterion max_points sum {criteria_total} does not match "
                f"item max_points {self.max_points}"
            )
        return self


class AssignmentConfig(_ConfigModel):
    id: NonEmptyStr
    title: NonEmptyStr
    total_points: PositiveInt
    feedback_language: NonEmptyStr = "id"
    overall_feedback_below: NonNegativeInt = 80

    @model_validator(mode="after")
    def validate_feedback_threshold(self) -> Self:
        if self.overall_feedback_below > self.total_points:
            raise ValueError(
                "overall_feedback_below must be between 0 and total_points "
                f"{self.total_points}"
            )
        return self


class RubricConfig(_ConfigModel):
    schema_version: Literal[1]
    assignment: AssignmentConfig
    items: list[ItemConfig] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value

    @model_validator(mode="after")
    def validate_rubric(self) -> Self:
        item_ids: set[str] = set()
        for item in self.items:
            if item.id in item_ids:
                raise ValueError(f"duplicate item id: {item.id}")
            item_ids.add(item.id)

        criterion_ids: set[str] = set()
        for item in self.items:
            for criterion in item.criteria:
                if criterion.id in criterion_ids:
                    raise ValueError(f"duplicate criterion id: {criterion.id}")
                criterion_ids.add(criterion.id)

        items_total = sum(item.max_points for item in self.items)
        if items_total != self.assignment.total_points:
            raise ValueError(
                f"item max_points sum {items_total} does not match assignment "
                f"total_points {self.assignment.total_points}"
            )

        return self


class RubricCatalogMeta(_ConfigModel):
    id: NonEmptyStr
    feedback_language: NonEmptyStr = "id"
    overall_feedback_below: int = Field(ge=0, le=100)


class CatalogAssignment(_ConfigModel):
    id: NonEmptyStr
    title: NonEmptyStr
    total_points: Literal[100]
    questions: list[ItemConfig] = Field(min_length=1)

    @field_validator("total_points", mode="before")
    @classmethod
    def validate_total_points(cls, value: object) -> object:
        if type(value) is not int or value != 100:
            raise ValueError("assignment total_points must be integer 100")
        return value

    @model_validator(mode="after")
    def validate_questions(self) -> Self:
        question_ids = [question.id for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("duplicate catalog question id")

        criterion_ids = [
            criterion.id
            for question in self.questions
            for criterion in question.criteria
        ]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("duplicate catalog criterion id")

        question_total = sum(question.max_points for question in self.questions)
        if question_total != self.total_points:
            raise ValueError(
                f"catalog question max_points sum {question_total} does not match "
                f"assignment total_points {self.total_points}"
            )
        return self


class RubricCatalog(_ConfigModel):
    schema_version: Literal[3]
    rubric: RubricCatalogMeta
    assignments: list[CatalogAssignment] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 3:
            raise ValueError("schema_version must be integer 3")
        return value

    @model_validator(mode="after")
    def validate_assignments(self) -> Self:
        assignment_ids = [assignment.id for assignment in self.assignments]
        if len(set(assignment_ids)) != len(assignment_ids):
            raise ValueError("duplicate catalog assignment id")
        return self


class AssignmentSelectorMeta(_ConfigModel):
    id: NonEmptyStr


class AssignmentSelector(_ConfigModel):
    schema_version: Literal[1]
    assignment: AssignmentSelectorMeta

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value


class RubricLoadError(ValueError):
    """Raised when a rubric file cannot be read as YAML."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[object, object]:
        self.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found unhashable key",
                    key_node.start_mark,
                )
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_rubric(path: Path) -> RubricConfig:
    return _load_model(path, RubricConfig, "rubric")


def load_rubric_catalog(path: Path) -> RubricCatalog:
    return _load_model(path, RubricCatalog, "rubric catalog")


def load_assignment_selector(path: Path) -> AssignmentSelector:
    return _load_model(path, AssignmentSelector, "assignment selector")


def select_catalog_assignment(
    catalog: RubricCatalog, assignment_id: str
) -> RubricConfig:
    """Convert one validated catalog assignment to the runtime rubric."""
    assignment = next(
        (item for item in catalog.assignments if item.id == assignment_id),
        None,
    )
    if assignment is None:
        raise RubricLoadError(f"unknown assignment: {assignment_id}")
    return RubricConfig(
        schema_version=1,
        assignment=AssignmentConfig(
            id=assignment.id,
            title=assignment.title,
            total_points=assignment.total_points,
            feedback_language=catalog.rubric.feedback_language,
            overall_feedback_below=catalog.rubric.overall_feedback_below,
        ),
        items=assignment.questions,
    )


def _load_model(path: Path, model: type[ModelT], label: str) -> ModelT:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RubricLoadError(f"Unable to read {label} at {path}: {exc}") from exc

    try:
        data = yaml.load(content, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise RubricLoadError(f"Invalid YAML {label} at {path}: {exc}") from exc

    if data is None:
        raise RubricLoadError(f"{label.title()} YAML at {path} is empty")

    return model.model_validate(data)
