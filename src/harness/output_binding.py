"""Validation and resolution for explicit cross-step output references."""

from dataclasses import dataclass
import re
from typing import Any, Iterator


REFERENCE_KEY = "$from"
REFERENCE_PATTERN = re.compile(
    r"^(?P<step>[A-Za-z0-9_-]+)\.output(?:\.(?P<path>[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*))?$"
)


class OutputBindingError(ValueError):
    """Raised when a planned output reference cannot be resolved safely."""


@dataclass(frozen=True)
class OutputReference:
    step_id: str
    path: tuple[str, ...]
    expression: str


def iter_output_references(value: Any) -> Iterator[OutputReference]:
    if isinstance(value, dict):
        if REFERENCE_KEY in value:
            if set(value) != {REFERENCE_KEY}:
                raise OutputBindingError(
                    "An output reference object may contain only '$from'"
                )
            expression = value[REFERENCE_KEY]
            if not isinstance(expression, str):
                raise OutputBindingError("An output reference must be a string")
            match = REFERENCE_PATTERN.fullmatch(expression)
            if match is None:
                raise OutputBindingError(
                    "Output references must use '<step>.output[.<field>]'"
                )
            path = tuple(match.group("path").split(".")) if match.group("path") else ()
            yield OutputReference(
                step_id=match.group("step"),
                path=path,
                expression=expression,
            )
            return
        for item in value.values():
            yield from iter_output_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_output_references(item)


def resolve_output_references(
    value: Any,
    completed_outputs: dict[str, Any],
) -> Any:
    if isinstance(value, dict):
        references = list(iter_output_references(value)) if REFERENCE_KEY in value else []
        if references:
            reference = references[0]
            try:
                resolved = completed_outputs[reference.step_id]
            except KeyError as exc:
                raise OutputBindingError(
                    f"Step output is not available: {reference.step_id}"
                ) from exc
            for field_name in reference.path:
                if not isinstance(resolved, dict) or field_name not in resolved:
                    raise OutputBindingError(
                        f"Output field is not available: {reference.expression}"
                    )
                resolved = resolved[field_name]
            return resolved
        return {
            key: resolve_output_references(item, completed_outputs)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            resolve_output_references(item, completed_outputs) for item in value
        ]
    return value


def schema_contains_path(schema: dict[str, Any], path: tuple[str, ...]) -> bool:
    current = schema
    definitions = schema.get("$defs", {})
    for field_name in path:
        reference = current.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            current = definitions.get(reference.rsplit("/", 1)[-1], {})
        properties = current.get("properties")
        if not isinstance(properties, dict) or field_name not in properties:
            return False
        current = properties[field_name]
    return True
