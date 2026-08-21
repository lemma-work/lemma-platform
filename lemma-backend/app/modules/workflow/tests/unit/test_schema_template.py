"""Form input_schema binding resolution and validation."""

import pytest

from app.modules.workflow.domain.context import RunContext, RunContextReader
from app.modules.workflow.domain.errors import ContextPathError, NodeExecutionError
from app.modules.workflow.domain.schema_template import (
    defaults_from_schema,
    iter_schema_expressions,
    resolve_schema_template,
    validate_resolved_schema,
)


def _reader(**nodes) -> RunContextReader:
    ctx = RunContext()
    for node_id, output in nodes.items():
        ctx.record_node_output(node_id, output)
    return RunContextReader(ctx)


def _expr(value: str, *, optional: bool = False) -> dict:
    binding = {"type": "expression", "value": value}
    if optional:
        binding["optional"] = True
    return binding


def test_iter_schema_expressions_finds_all_expression_bindings():
    schema = {
        "type": "object",
        "properties": {
            "a": {"enum": _expr("src.labels")},
            "b": {"default": _expr("src.draft", optional=True)},
            "c": {"type": "string"},
            "d": {"const": {"type": "literal", "value": "x"}},  # literal: no expr
        },
    }
    assert set(iter_schema_expressions(schema)) == {"src.labels", "src.draft"}


def test_iter_schema_expressions_handles_none_and_scalars():
    assert list(iter_schema_expressions(None)) == []
    assert list(iter_schema_expressions("plain")) == []
    assert list(iter_schema_expressions([{"enum": _expr("x")}])) == ["x"]


def test_resolve_replaces_bindings_and_deep_copies():
    schema = {
        "properties": {
            "category": {"type": "string", "enum": _expr("src.labels")},
            "body": {"type": "string", "default": _expr("src.draft")},
            "channel": {"type": "string", "default": {"type": "literal", "value": "x"}},
        }
    }
    reader = _reader(src={"labels": ["x", "y"], "draft": "hello"})
    resolved = resolve_schema_template(schema, reader)

    assert resolved["properties"]["category"]["enum"] == ["x", "y"]
    assert resolved["properties"]["body"]["default"] == "hello"
    assert resolved["properties"]["channel"]["default"] == "x"
    # original template is untouched (deep copy)
    assert schema["properties"]["category"]["enum"] == _expr("src.labels")


def test_required_binding_missing_raises():
    reader = _reader(src={"labels": ["x"]})
    with pytest.raises(ContextPathError):
        resolve_schema_template({"default": _expr("src.nope")}, reader)


def test_optional_binding_missing_resolves_to_none():
    reader = _reader(src={"labels": ["x"]})
    resolved = resolve_schema_template(
        {"default": _expr("src.nope", optional=True)}, reader
    )
    assert resolved == {"default": None}


def test_plain_subschema_is_not_a_binding():
    """A normal subschema (type=string/object/...) passes through untouched."""
    reader = _reader(src={"labels": ["x"]})
    schema = {"type": "object", "properties": {"c": {"type": "string"}}}
    assert resolve_schema_template(schema, reader) == schema


def test_validate_resolved_schema_rejects_missing_or_empty_enum():
    with pytest.raises(NodeExecutionError) as exc:
        validate_resolved_schema("intake", {"properties": {"c": {"enum": None}}})
    assert "c" in str(exc.value)

    with pytest.raises(NodeExecutionError):
        validate_resolved_schema("intake", {"properties": {"c": {"enum": []}}})


def test_validate_resolved_schema_accepts_nonempty_enum_and_null_default():
    validate_resolved_schema(
        "intake",
        {"properties": {"c": {"enum": ["a"]}, "d": {"default": None}}},
    )


def test_defaults_from_schema_extracts_top_level_defaults():
    schema = {
        "properties": {
            "a": {"default": "x"},
            "b": {"type": "string"},
            "c": {"default": None},
        }
    }
    assert defaults_from_schema(schema) == {"a": "x", "c": None}


def test_defaults_from_schema_handles_non_object_schema():
    assert defaults_from_schema(None) == {}
    assert defaults_from_schema({"type": "string"}) == {}
