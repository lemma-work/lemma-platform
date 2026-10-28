"""What a tool is, in Lemma's terms: a ``ToolSpec`` carrying its ``ToolPolicy``.

A toolset is a module in ``infrastructure/tools/`` exporting ``SPECS``: plain
async functions taking a ``ToolContext`` and a request model. Every surface
reads the same specs -- the in-process harness through
``harness/lemma/toolset_adapter``, MCP clients through
``mcp/toolspec_adapter`` -- and every call runs through
``services/tool_runtime``. What used to be a dozen tables keyed by tool name
(pausing, outsider allow-list, MCP annotations, deferral, metrics kind) is the
policy on the spec.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import CoreConfig, SchemaValidator, core_schema

from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import JsonObject


class ToolAccess(StrEnum):
    """What a call does to the pod; an outside MCP client is granted one."""

    READ = "read"
    WRITE = "write"


#: The planning tool, whose stored returns the progress readers parse back.
TODO_TOOL_NAME = "write_todos"


class ReplayClass(StrEnum):
    """What a resumed turn may do with a call its last attempt left open
    (``domain/replay``): run it again, run it again under its recorded id, or
    never."""

    SAFE = "SAFE"
    KEYED = "KEYED"
    UNSAFE = "UNSAFE"


#: A call's replay class read off its arguments, for a tool whose class depends
#: on what it was asked.
type ReplayRefinement = Callable[[Mapping[str, object]], ReplayClass]

#: Which family a call belongs to, for the tool-call counter.
type ToolKind = Literal["connector", "function", "agent", "sandbox", "builtin"]


class ToolGroup(StrEnum):
    """The bundle a spec belongs to.

    The first members are the declarable ``AgentToolset`` values; the rest are
    added per run rather than declared.
    """

    WORKSPACE_CLI = "WORKSPACE_CLI"
    BROWSER = "BROWSER"
    SKILLS = "SKILLS"
    WEB_SEARCH = "WEB_SEARCH"
    USER_INTERACTION = "USER_INTERACTION"
    SPEECH = "SPEECH"
    POD = "POD"
    SUBAGENTS = "SUBAGENTS"
    TODO = "TODO"
    CONNECTORS = "CONNECTORS"
    WAIT = "WAIT"
    MESSAGING = "MESSAGING"
    VIEW_IMAGE = "VIEW_IMAGE"
    VM_BROWSER = "VM_BROWSER"
    QUESTIONS = "QUESTIONS"
    CONTACT = "CONTACT"
    FORM = "FORM"
    CALL = "CALL"
    CALLABLES = "CALLABLES"
    SURFACE = "SURFACE"
    FINAL_ANSWER = "FINAL_ANSWER"
    MEMORY_PASS = "MEMORY_PASS"
    #: ``search_tools`` and ``call_tool``: the fixed entries to the deferred
    #: tools where the provider has no channel for tools added mid-run.
    DEFERRED = "DEFERRED"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Everything about a tool besides its schema, decided once on the spec."""

    access: ToolAccess = ToolAccess.WRITE
    destructive: bool = False
    idempotent: bool = True
    #: Reaches beyond the pod (a public file link anyone can open).
    open_world: bool = False
    #: How an outside MCP client is shown it, and the MCP Apps view that
    #: renders its result.
    title: str | None = None
    view: str | None = None
    #: Ends the run until something resolves it. Never derived from the name.
    pauses: PauseKind | None = None
    #: Read and found to reach nothing but what the pod made Public, so a run
    #: answering somebody outside the pod may call it. Everything else is
    #: withheld from such a run.
    outsider_allowed: bool = False
    #: Hidden behind tool search on the pod default assistant's runs.
    deferrable: bool = False
    #: Duplicates the coding agent's own shell, so withheld when an Agent Host
    #: run executes natively on the person's Mac.
    native_command: bool = False
    kind: ToolKind = "builtin"
    #: Undeclared is UNSAFE: an honest "outcome unknown" beats a duplicate.
    replay: ReplayClass | ReplayRefinement = ReplayClass.UNSAFE


@dataclass(frozen=True, slots=True)
class ImagePart:
    """Image content in a prompt; the provider turns it into its own type."""

    data: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class WithImages:
    """A tool's result plus images the model sees beside it."""

    value: object
    images: tuple[ImagePart, ...]


# A request model whose fields are the tool's arguments themselves: its schema
# is sent without a title and refuses unknown arguments, which is how a tool
# taking plain parameters was always described. (A docstring here, or on a
# subclass, would become the schema's description.)
class FlatArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flat: ClassVar[bool] = True


# A tool that takes no arguments. No docstring: it would become the schema's
# description.
class NoArguments(FlatArguments):
    pass


type ToolFunction = Callable[[Any, Any], Awaitable[object]]
#: A per-run description: the static one in, what this run is shown out.
type Describer = Callable[[Any, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool, as every surface shows and runs it."""

    name: str
    description: str
    #: The request model; ``None`` when the arguments are a free JSON object
    #: described by ``json_schema`` alone (a pod function's own input schema).
    parameters: type[BaseModel] | None
    json_schema: JsonObject
    toolset: ToolGroup
    run: ToolFunction
    policy: ToolPolicy = ToolPolicy()
    #: The keyword the request model travels under when a client wraps it.
    argument: str = "request"
    describe: Describer | None = None
    strict: bool | None = None
    validator: SchemaValidator | None = field(default=None, compare=False)

    def parse(self, arguments: Mapping[str, object]) -> object:
        """The call's arguments as the tool takes them, or ``ValidationError``.

        A client may send the request model's fields, or wrap them under
        ``argument``; both are accepted, as pydantic-ai accepts them.
        """
        if self.parameters is None or self.validator is None:
            return dict(arguments)
        if list(arguments) == [self.argument]:
            inner = arguments[self.argument]
            properties = self.json_schema.get("properties")
            if not isinstance(properties, dict) or self.argument not in properties:
                return self.validator.validate_python(inner)
            try:
                return self.validator.validate_python(arguments)
            except ValidationError:
                return self.validator.validate_python(inner)
        return self.validator.validate_python(dict(arguments))


class _ToolJsonSchema(GenerateJsonSchema):
    """Property titles dropped, as every model-facing tool schema has them."""

    def _named_required_fields_schema(
        self, named_required_fields: Sequence[tuple[str, bool, Any]]
    ) -> JsonSchemaValue:
        schema = super()._named_required_fields_schema(named_required_fields)
        for prop in schema.get("properties", {}):
            schema["properties"][prop].pop("title", None)
        return schema


def model_schema(model: type[BaseModel]) -> JsonObject:
    """The JSON schema a tool taking ``model`` is described by."""
    schema = model.model_json_schema(schema_generator=_ToolJsonSchema)
    if getattr(model, "flat", False):
        schema.pop("title", None)
    return schema


def tool(
    run: ToolFunction,
    toolset: ToolGroup,
    policy: ToolPolicy = ToolPolicy(),
    *,
    description: str | None = None,
    describe: Describer | None = None,
) -> ToolSpec:
    """A spec from a tool function: its name, docstring and request model."""
    _, argument = inspect.signature(run).parameters
    parameters = get_type_hints(run)[argument]
    return ToolSpec(
        name=run.__name__,
        description=description or inspect.cleandoc(run.__doc__ or ""),
        parameters=parameters,
        json_schema=model_schema(parameters),
        toolset=toolset,
        run=run,
        policy=policy,
        argument=argument,
        describe=describe,
        validator=SchemaValidator(
            core_schema.no_info_after_validator_function(
                lambda value: value, parameters.__pydantic_core_schema__
            ),
            CoreConfig(title=run.__name__),
        ),
    )


class AgentInputRequired(Exception):
    """Control-flow signal: the run pauses until its pause is resolved.

    Raised by a pausing tool inside the in-process harness instead of blocking
    the worker. The call and its pause are already persisted; the harness ends
    the run cleanly and a fresh run replays the synthesized return.
    ``tool_call_id`` is the durable pause id. ``kind`` is the name the call is
    stored under, which the WAITING event carries on to the surfaces.
    """

    def __init__(self, tool_call_id: str, kind: PauseKind | str) -> None:
        from app.modules.agent.domain.pauses import CALL_NAMES

        self.tool_call_id = tool_call_id
        self.kind: str = CALL_NAMES[kind] if isinstance(kind, PauseKind) else kind
        super().__init__(
            f"Agent run paused for user input ({self.kind}:{tool_call_id})"
        )


__all__ = [
    "AgentInputRequired",
    "FlatArguments",
    "ImagePart",
    "NoArguments",
    "ReplayClass",
    "TODO_TOOL_NAME",
    "ToolAccess",
    "ToolGroup",
    "ToolKind",
    "ToolPolicy",
    "ToolSpec",
    "WithImages",
    "model_schema",
    "tool",
]
