"""Tool JSON schemas must not contain typeless `{"default": ...}` nodes.

Strict providers (e.g. Fireworks) reject a schema node that carries a `default`
(or other metadata) but no type-defining key with "could not understand the
instance `{'default': None}`". A bare `Any` field serializes to exactly that, so
we guard the whole pod-default tool surface against it.
"""

from __future__ import annotations

from uuid import uuid4

from pydantic_ai.tools import Tool

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import ConversationType
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.tools.final_answer import get_final_answer_tool
from app.modules.agent.tools.registry import (
    POD_DEFAULT_AGENT_TOOLSETS,
    _TOOLSET_BY_NAME,
)

_TYPE_KEYS = {
    "type",
    "anyOf",
    "oneOf",
    "allOf",
    "$ref",
    "enum",
    "const",
    "properties",
    "items",
    "additionalProperties",
}


def _typeless_default_nodes(node: object, path: str = "$") -> list[tuple[str, dict]]:
    """Find schema nodes that carry a `default` but no type-defining key."""
    bad: list[tuple[str, dict]] = []
    if isinstance(node, dict):
        if "default" in node and not (node.keys() & _TYPE_KEYS):
            bad.append((path, dict(node)))
        for key, value in node.items():
            bad += _typeless_default_nodes(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            bad += _typeless_default_nodes(value, f"{path}[{index}]")
    return bad


def _tool_schema(tool: object) -> dict | None:
    function_schema = getattr(tool, "function_schema", None)
    if function_schema is not None and getattr(function_schema, "json_schema", None):
        return function_schema.json_schema
    tool_def = getattr(tool, "tool_def", None)
    if tool_def is not None:
        return tool_def.parameters_json_schema
    return None


def _toolset_schemas(label: str, toolset: object) -> list[tuple[str, dict]]:
    schemas: list[tuple[str, dict]] = []
    tools = getattr(toolset, "tools", None) or {}
    for tool_name, tool in tools.items():
        schema = _tool_schema(tool)
        if schema is not None:
            schemas.append((f"{label}/{tool_name}", schema))
    return schemas


def _pod_default_tool_schemas() -> list[tuple[str, dict]]:
    schemas: list[tuple[str, dict]] = []
    for toolset_name in POD_DEFAULT_AGENT_TOOLSETS:
        # Capability-only toolsets (e.g. TODO) aren't static singletons; their
        # schemas are validated separately below.
        toolset = _TOOLSET_BY_NAME.get(toolset_name)
        if toolset is None:
            continue
        schemas += _toolset_schemas(toolset_name.value, toolset)
    # The TODO capability's tools are part of the pod-default surface too, so they
    # must also be free of typeless-default nodes that strict providers reject.
    from app.modules.agent.capabilities.todo import build_todo_capability

    todo_toolset = build_todo_capability(
        uow_factory=lambda: None,  # not invoked during schema extraction
        conversation_id=uuid4(),
    ).get_toolset()
    schemas += _toolset_schemas("TODO", todo_toolset)
    return schemas


def test_pod_default_tool_schemas_have_no_typeless_default_nodes():
    schemas = _pod_default_tool_schemas()
    assert schemas, "expected to resolve pod-default tool schemas"

    offenders = {
        name: bad
        for name, schema in schemas
        if (bad := _typeless_default_nodes(schema))
    }
    assert not offenders, f"typeless default schema nodes found: {offenders}"


def _unresolved_refs(node: object, path: str = "$") -> list[str]:
    """Find `$ref`/`$defs` nodes left in a schema after transformation."""
    refs: list[str] = []
    if isinstance(node, dict):
        if "$ref" in node:
            refs.append(f"{path} -> {node['$ref']}")
        if "$defs" in node:
            refs.append(f"{path}.$defs ({list(node['$defs'])})")
        for key, value in node.items():
            refs += _unresolved_refs(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            refs += _unresolved_refs(value, f"{path}[{index}]")
    return refs


def test_openai_compatible_transformer_inlines_all_defs():
    """OpenAI-compatible providers (e.g. Fireworks GLM) can't resolve `$ref`
    server-side; the transformer must inline every (non-recursive) reference so
    no provider-side resolution is needed. Guards the whole pod-default surface
    against "Error resolving schema reference '#/$defs/...'".
    """
    from app.modules.agent.services.openai_schema_compat import (
        InlineDefsOpenAIJsonSchemaTransformer,
    )

    schemas = _pod_default_tool_schemas()
    assert schemas, "expected to resolve pod-default tool schemas"

    offenders = {
        name: unresolved
        for name, schema in schemas
        if (
            unresolved := _unresolved_refs(
                InlineDefsOpenAIJsonSchemaTransformer(schema, strict=None).walk()
            )
        )
    }
    assert not offenders, f"unresolved $ref/$defs after inlining: {offenders}"


def test_pod_write_record_data_advertises_object_or_json_string():
    """`pod_write_record.data` must offer a JSON-string form alongside the object.

    OpenAI requires every object schema to carry a `properties` map, so a
    free-form (dynamic-column) object necessarily serializes with
    `properties: {}` — which many models read as "no fields" and fill with `{}`,
    silently dropping the row. The JSON-string branch is the escape hatch that
    lets those models write data, so the transformed tool schema must keep both
    branches (and never collapse `data` to a strict, closed empty object).
    """
    from app.modules.agent.services.openai_schema_compat import (
        InlineDefsOpenAIJsonSchemaTransformer,
    )
    from app.modules.agent.tools.pod.pydantic_adapter import pod_toolset

    raw = _tool_schema(pod_toolset.tools["pod_write_record"])
    assert raw is not None
    transformed = InlineDefsOpenAIJsonSchemaTransformer(raw, strict=None).walk()
    data_schema = transformed["properties"]["data"]
    branch_types = {branch.get("type") for branch in data_schema.get("anyOf", [])}
    assert {"object", "string"} <= branch_types, data_schema


def test_record_filter_value_is_typed_not_bare_any():
    from app.modules.agent.tools.pod.models import RecordFilter

    value_schema = RecordFilter.model_json_schema()["properties"]["value"]
    # The value must carry a type-defining key (anyOf of scalars/list), not be a
    # bare `{"default": null}` node.
    assert value_schema.keys() & _TYPE_KEYS
    assert not _typeless_default_nodes(RecordFilter.model_json_schema())


def test_final_answer_tool_schema_clean_without_output_schema():
    agent = Agent(pod_id=uuid4(), user_id=uuid4(), name="x", instruction="", toolsets=[])
    assert agent.output_schema is None
    tool = Tool(get_final_answer_tool(agent), takes_ctx=True)
    schema = tool.function_schema.json_schema
    assert not _typeless_default_nodes(schema)


def _runner() -> AgentRunnerService:
    return AgentRunnerService(uow_factory=object(), harness_registry=object())


def test_task_always_gets_final_answer_tool_chat_never_does():
    # The final_answer tool drives the TASK lifecycle (status WAITING/COMPLETED),
    # so every TASK conversation gets it regardless of output schema. CHAT never
    # does (plain text). The output *schema* is only applied when configured.
    runner = _runner()
    pod_id, user_id = uuid4(), uuid4()
    task = Conversation(pod_id=pod_id, user_id=user_id, type=ConversationType.TASK)
    chat = Conversation(pod_id=pod_id, user_id=user_id, type=ConversationType.CHAT)

    plain_agent = Agent(
        pod_id=pod_id, user_id=user_id, name="plain", instruction="", toolsets=[]
    )
    schema_agent = Agent(
        pod_id=pod_id,
        user_id=user_id,
        name="structured",
        instruction="",
        toolsets=[],
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    )

    assert runner._resolve_output_type(plain_agent, task) is not None
    assert runner._resolve_output_type(schema_agent, task) is not None
    assert runner._resolve_output_type(plain_agent, chat) is None
    assert runner._resolve_output_type(schema_agent, chat) is None


def test_final_answer_tool_schema_clean_with_output_schema():
    agent = Agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="structured",
        instruction="",
        toolsets=[],
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )
    tool = Tool(get_final_answer_tool(agent), takes_ctx=True)
    assert not _typeless_default_nodes(tool.function_schema.json_schema)
