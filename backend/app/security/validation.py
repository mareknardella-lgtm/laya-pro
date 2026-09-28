"""Input and output schema validation helpers."""

from __future__ import annotations

from typing import Type

from pydantic import BaseModel, ValidationError


class SchemaValidationError(ValueError):
    """Data is not valid for a registered schema."""


def validate_model(model: Type[BaseModel], value: object) -> BaseModel:
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise SchemaValidationError("Data does not match the registered schema") from exc
