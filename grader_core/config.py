from collections.abc import Hashable
import math
from pathlib import Path
from typing import Annotated, Literal, Self, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
UnitFloat = Annotated[float, Field(ge=0, le=1)]
PositiveUnitFloat = Annotated[float, Field(gt=0, le=1)]
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


class TemplateLevel(_ConfigModel):
    fraction: UnitFloat
    description: NonEmptyStr


class CriterionTemplate(_ConfigModel):
    id: NonEmptyStr
    weight: PositiveUnitFloat
    description: NonEmptyStr
    required_evidence: NonEmptyStr
    levels: list[TemplateLevel] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_levels(self) -> Self:
        fractions = [level.fraction for level in self.levels]
        if len(set(fractions)) != len(fractions):
            raise ValueError("duplicate template level fraction")
        if 0.0 not in fractions:
            raise ValueError("template levels must include fraction 0.0")
        if 1.0 not in fractions:
            raise ValueError("template levels must include fraction 1.0")
        return self


class RubricTemplateMeta(_ConfigModel):
    id: NonEmptyStr
    feedback_language: NonEmptyStr = "id"
    overall_feedback_below: int = Field(ge=0, le=100)


class RubricTemplate(_ConfigModel):
    schema_version: Literal[2]
    rubric: RubricTemplateMeta
    criteria: list[CriterionTemplate] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("schema_version must be integer 2")
        return value

    @model_validator(mode="after")
    def validate_criteria(self) -> Self:
        criterion_ids = [criterion.id for criterion in self.criteria]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("duplicate template criterion id")
        weight_total = sum(criterion.weight for criterion in self.criteria)
        if not math.isclose(weight_total, 1.0, abs_tol=1e-9):
            raise ValueError(f"template weights must sum to 1, got {weight_total}")
        return self


class ManifestQuestion(_ConfigModel):
    id: NonEmptyStr
    label: NonEmptyStr
    max_points: PositiveInt


class ManifestAssignment(_ConfigModel):
    id: NonEmptyStr
    title: NonEmptyStr
    total_points: Literal[100]

    @field_validator("total_points", mode="before")
    @classmethod
    def validate_total_points(cls, value: object) -> object:
        if type(value) is not int or value != 100:
            raise ValueError("assignment total_points must be integer 100")
        return value


class AssignmentManifest(_ConfigModel):
    schema_version: Literal[1]
    assignment: ManifestAssignment
    questions: list[ManifestQuestion] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value

    @model_validator(mode="after")
    def validate_questions(self) -> Self:
        question_ids = [question.id for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("duplicate manifest question id")
        question_total = sum(question.max_points for question in self.questions)
        if question_total != self.assignment.total_points:
            raise ValueError(
                f"question max_points sum {question_total} does not match "
                f"assignment total_points {self.assignment.total_points}"
            )
        return self


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


def load_rubric_template(path: Path) -> RubricTemplate:
    return _load_model(path, RubricTemplate, "rubric template")


def load_assignment_manifest(path: Path) -> AssignmentManifest:
    return _load_model(path, AssignmentManifest, "assignment manifest")


def build_effective_rubric(
    template: RubricTemplate, manifest: AssignmentManifest
) -> RubricConfig:
    """Expand shared criteria and weekly question points into a strict rubric."""
    criteria_points = {
        question.id: _allocate_points(
            question.max_points,
            [criterion.weight for criterion in template.criteria],
        )
        for question in manifest.questions
    }
    items: list[ItemConfig] = []
    for question in manifest.questions:
        criteria = [
            CriterionConfig(
                id=f"{question.id}__{criterion.id}",
                description=criterion.description,
                max_points=criteria_points[question.id][index],
                required_evidence=criterion.required_evidence,
                levels=_scaled_levels(
                    criterion.levels,
                        criteria_points[question.id][index],
                ),
            )
            for index, criterion in enumerate(template.criteria)
        ]
        items.append(
            ItemConfig(
                id=question.id,
                label=question.label,
                max_points=question.max_points,
                criteria=criteria,
            )
        )

    return RubricConfig(
        schema_version=1,
        assignment=AssignmentConfig(
            id=manifest.assignment.id,
            title=manifest.assignment.title,
            total_points=manifest.assignment.total_points,
            feedback_language=template.rubric.feedback_language,
            overall_feedback_below=template.rubric.overall_feedback_below,
        ),
        items=items,
    )


def _allocate_points(total_points: int, weights: list[float]) -> tuple[int, ...]:
    if total_points < len(weights):
        raise ValueError(
            f"question max_points {total_points} is too small for "
            f"{len(weights)} criteria"
        )

    raw_points = [total_points * weight for weight in weights]
    allocated = [math.floor(value) for value in raw_points]
    remaining = total_points - sum(allocated)
    order = sorted(
        range(len(weights)),
        key=lambda index: (raw_points[index] - allocated[index], -index),
        reverse=True,
    )
    for index in order[:remaining]:
        allocated[index] += 1

    if any(points <= 0 for points in allocated):
        raise ValueError(
            "criterion point allocation produced a zero-point criterion; "
            "increase the question max_points or reduce template criteria"
        )
    return tuple(allocated)


def _scaled_levels(
    levels: list[TemplateLevel], max_points: int
) -> list[ScoreLevel]:
    scaled: list[ScoreLevel] = []
    seen_scores: set[int] = set()
    for level in levels:
        score = round(level.fraction * max_points)
        if level.fraction == 0.0:
            score = 0
        elif level.fraction == 1.0:
            score = max_points
        if score in seen_scores:
            continue
        seen_scores.add(score)
        scaled.append(ScoreLevel(score=score, description=level.description))
    return scaled


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
