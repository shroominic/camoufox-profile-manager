"""Validated, database-owned Python scripts and their private runtime inputs."""

import ast
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
STAGE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_SOURCE_BYTES = 256 * 1024
MAX_INPUT_CHARS = 65_536
ACTIVE_STATUSES = frozenset({"queued", "running", "stopping"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
RUN_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
EVENT_KINDS = frozenset({"queued", "started", "stage", "stopping", *TERMINAL_STATUSES})


class ScriptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _label(value: str) -> str:
    if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Use a nonempty label without control characters")
    return value


class ScriptChoice(ScriptModel):
    value: str = Field(min_length=1, max_length=512)
    label: str = Field(min_length=1, max_length=160)

    _clean_label = field_validator("label")(_label)


class ScriptInput(ScriptModel):
    name: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    type: Literal["text", "secret", "integer", "boolean", "select", "profile", "proxy"]
    required: bool = False
    help: str = Field(default="", max_length=1000)
    default: str | int | bool | None = None
    choices: list[ScriptChoice] = Field(default_factory=list, max_length=100)

    _clean_label = field_validator("label")(_label)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("Use a valid input identifier")
        return value

    @model_validator(mode="after")
    def configuration(self):
        if self.type == "secret" and self.default is not None:
            raise ValueError("Secret inputs cannot have saved defaults")
        if self.type == "select":
            values = [choice.value for choice in self.choices]
            if not values or len(values) != len(set(values)):
                raise ValueError("Select inputs need unique choices")
        elif self.choices:
            raise ValueError("Only select inputs accept choices")
        if self.default is not None:
            _validate_value(self, self.default)
        return self


class ScriptStage(ScriptModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)

    _clean_label = field_validator("label")(_label)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value):
        if not STAGE_ID.fullmatch(value):
            raise ValueError("Use a lowercase stage identifier")
        return value


class ScriptDefinition(ScriptModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    language: Literal["python"] = "python"
    code: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    inputs: list[ScriptInput] = Field(default_factory=list, max_length=32)
    stages: list[ScriptStage] = Field(default_factory=list, max_length=32)
    max_runtime_seconds: int = Field(default=900, ge=5, le=7200)
    enabled: bool = True

    _clean_name = field_validator("name")(_label)

    @field_validator("code")
    @classmethod
    def valid_source(cls, value):
        if len(value.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("Script source exceeds the size limit")
        try:
            tree = ast.parse(value, filename="<database-script>", mode="exec")
            compile(tree, filename="<database-script>", mode="exec")
        except (SyntaxError, ValueError, RecursionError):
            raise ValueError("Script source is not valid Python") from None
        return value

    @model_validator(mode="after")
    def unique_identifiers(self):
        if len({item.name for item in self.inputs}) != len(self.inputs):
            raise ValueError("Input identifiers must be unique")
        if len({stage.id for stage in self.stages}) != len(self.stages):
            raise ValueError("Stage identifiers must be unique")
        return self


def definition_from_record(record: dict) -> ScriptDefinition:
    """Strip store metadata before validating a stored definition."""
    return ScriptDefinition.model_validate(
        {name: record[name] for name in ScriptDefinition.model_fields if name in record}
    )


def _validate_value(field: ScriptInput, value):
    if field.type == "integer":
        if type(value) is not int or not -(2**53 - 1) <= value <= 2**53 - 1:
            raise ValueError("Use an integer script input")
    elif field.type == "boolean":
        if type(value) is not bool:
            raise ValueError("Use a boolean script input")
    else:
        if not isinstance(value, str) or len(value) > MAX_INPUT_CHARS or "\x00" in value:
            raise ValueError("Use a bounded text script input")
        if field.type == "profile" and not re.fullmatch(r"[A-Za-z0-9]{8}", value):
            raise ValueError("Select a valid profile")
        if field.type == "proxy" and not re.fullmatch(r"[A-Za-z0-9]{8}", value):
            raise ValueError("Select a valid saved proxy")
        if field.type == "select" and value not in {choice.value for choice in field.choices}:
            raise ValueError("Select one of the available script choices")
    return value


def validate_run_inputs(definition: ScriptDefinition | dict, inputs: dict) -> dict:
    """Return normalized inputs in memory. Error messages never contain values."""
    if isinstance(definition, dict):
        definition = definition_from_record(definition)
    if not isinstance(inputs, dict) or len(inputs) > 32:
        raise ValueError("Supply a valid script input object")
    fields = {field.name: field for field in definition.inputs}
    if set(inputs) - set(fields):
        raise ValueError("Unknown script input")
    result = {}
    for name, field in fields.items():
        value = inputs.get(name, field.default)
        if value is None or (value == "" and field.type in {"secret", "profile", "proxy", "integer", "select"}):
            if field.required:
                raise ValueError("Supply all required script inputs")
            result[name] = None
            continue
        if field.required and isinstance(value, str) and not value:
            raise ValueError("Supply all required script inputs")
        result[name] = _validate_value(field, value)
    # Bound the whole private payload independently of the HTTP body limit.
    if len(json.dumps(result).encode("utf-8")) > 256 * 1024:
        raise ValueError("Script inputs exceed the size limit")
    return result
