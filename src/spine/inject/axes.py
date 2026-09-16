"""Transparent scorer axes: named products of already recorded scalar features."""

import math
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Feature = Literal["sem", "kw", "time", "proj", "freq", "hist", "loc", "thread", "where"]


class AxisNomination(BaseModel):
    """A curator's replayable definition, never executable generated code."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    operation: Literal["product"] = "product"
    inputs: tuple[Feature, Feature]
    weight: float = Field(ge=0, lt=1)
    action: Literal["axis_add", "axis_retire"]
    rationale: str = Field(min_length=1)
    provenance: dict

    def value(self, features: Mapping[str, float | None]) -> float | None:
        values = [features.get(name) for name in self.inputs]
        if any(value is None for value in values):
            return None
        return math.prod(values)


def apply_axes(
    score: float, features: Mapping[str, float | None], axes: Mapping[str, AxisNomination]
) -> float:
    for _name, axis in sorted(axes.items()):
        value = axis.value(features)
        if axis.weight and value is not None:
            score = (1 - axis.weight) * score + axis.weight * value
    return score
