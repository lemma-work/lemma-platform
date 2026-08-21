"""Resolve typed input bindings embedded in a form node's `input_schema`.

A form node's `input_schema`/`ui_schema` are JSON Schema *templates*: any value
may be a typed input binding — the very same `{"type": "expression", "value":
"..."}` / `{"type": "literal", "value": ...}` form that `input_mapping` uses
everywhere else in the workflow system — resolved against the run context
(`<node_id>.<field>`, `start.*`, `loop.*`) at suspend time. The form executor
resolves the template and puts the concrete schema on the wait row, so every
consumer (frontend, CLI, SDK) renders the form from the wait alone and never
resolves expressions itself.

Detection is unambiguous: `"expression"`/`"literal"` are not valid JSON Schema
`type` values, so a dict whose `type` is one of those is always a binding, never
a subschema. `optional` on an expression binding controls failure (consistent
with `input_mapping`): a required binding that resolves to nothing fails the
node — e.g. a dropdown's option source — while an optional one yields null,
e.g. an unfilled `default`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

from pydantic import TypeAdapter

from app.modules.workflow.domain.errors import NodeExecutionError
from app.modules.workflow.domain.nodes.bindings import (
    ExpressionInputBinding,
    InputBinding,
    LiteralInputBinding,
)

if TYPE_CHECKING:
    from app.modules.workflow.domain.context import ContextReader

_BINDING_ADAPTER: TypeAdapter[InputBinding] = TypeAdapter(InputBinding)
_BINDING_TYPES = {"expression", "literal"}
_ENUM_KEYS = ("enum", "enumNames")


def _as_binding(node: Any) -> InputBinding | None:
    """Return the typed binding for a binding-shaped dict, or None for a plain
    schema fragment. A dict whose `type` is `expression`/`literal` is always a
    binding (those are not valid JSON Schema types); a structurally invalid one
    raises ``pydantic.ValidationError`` (surfaced at save time)."""
    if not isinstance(node, dict) or node.get("type") not in _BINDING_TYPES:
        return None
    return _BINDING_ADAPTER.validate_python(node)


def iter_schema_expressions(schema: Any) -> Iterator[str]:
    """Yield every expression-binding `value` anywhere in the schema, for
    save-time compile checking."""
    binding = _as_binding(schema)
    if binding is not None:
        if isinstance(binding, ExpressionInputBinding):
            yield binding.value
        return
    if isinstance(schema, dict):
        for value in schema.values():
            yield from iter_schema_expressions(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from iter_schema_expressions(item)


def resolve_schema_template(schema: Any, reader: "ContextReader") -> Any:
    """Deep-copy `schema` with every embedded binding replaced by its resolved
    value. Required expressions that resolve to nothing raise ContextPathError;
    optional ones yield None. Never mutates the input."""
    binding = _as_binding(schema)
    if binding is not None:
        return _resolve_binding(binding, reader)
    if isinstance(schema, dict):
        return {
            key: resolve_schema_template(value, reader) for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [resolve_schema_template(item, reader) for item in schema]
    return schema


def _resolve_binding(binding: InputBinding, reader: "ContextReader") -> Any:
    if isinstance(binding, LiteralInputBinding):
        return binding.value
    if binding.optional:
        return reader.resolve(binding.value)
    return reader.resolve_required(binding.value)


def validate_resolved_schema(node_id: str, schema: Any) -> None:
    """Light structural check on the resolved schema: every ``enum``/``enumNames``
    must be a non-empty list, so a dropdown whose option source resolved to
    nothing (an optional binding, or a static mistake) fails loudly instead of
    rendering an empty, unusable control."""
    for field, value in _iter_enums(schema):
        if not isinstance(value, list) or len(value) == 0:
            raise NodeExecutionError(
                node_id,
                f"form schema '{field}' resolved to {value!r}, which is not a "
                "non-empty list of options",
            )


def defaults_from_schema(schema: Any) -> dict[str, Any]:
    """Top-level property defaults from a resolved object schema, used to fill
    fields the user omitted on form submit. Nested defaults are out of scope —
    form fields are flat object properties."""
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {
        key: prop["default"]
        for key, prop in properties.items()
        if isinstance(prop, dict) and "default" in prop
    }


def _iter_enums(schema: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in _ENUM_KEYS:
                yield (path or key, value)
            else:
                child = f"{path}.{key}" if path else key
                yield from _iter_enums(value, child)
    elif isinstance(schema, list):
        for index, item in enumerate(schema):
            yield from _iter_enums(item, f"{path}[{index}]")
