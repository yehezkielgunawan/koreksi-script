from collections.abc import Hashable
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


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
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RubricLoadError(f"Unable to read rubric at {path}: {exc}") from exc

    try:
        data = yaml.load(content, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise RubricLoadError(f"Invalid YAML rubric at {path}: {exc}") from exc

    if data is None:
        raise RubricLoadError(f"Rubric YAML at {path} is empty")

    return RubricConfig.model_validate(data)
