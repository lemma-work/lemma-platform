"""Typed run context — the modelled contract for workflow execution state.

Shape, as seen by JMESPath expressions:

    start.payload.*  / start.metadata.* / start.llm_output.*   trigger data
    loop.item / loop.index / loop.count / loop.<item_var>      innermost loop
    <node_id>.<field>                                          node outputs

There is no root-level merging and node outputs are always dicts, so
`<node_id>.<field>` always resolves. Mutations go through the three writer
methods only; executors receive a read-only ContextReader.
"""

from typing import Any, Mapping, Protocol

from pydantic import BaseModel, Field

from app.modules.workflow.domain.errors import ContextPathError
from app.modules.workflow.domain.expressions import ExpressionEngine
from app.modules.workflow.domain.nodes import (
    ExpressionInputBinding,
    InputBinding,
    RESERVED_NODE_IDS,
)
from app.modules.workflow.domain.start import WorkflowStartType


class TriggerContext(BaseModel):
    """Trigger payload for scheduled/event/datastore-started runs.

    This is the only thing that ever populates the `start` namespace; manual
    runs have no `start` at all.
    """

    trigger_type: WorkflowStartType = WorkflowStartType.SCHEDULED
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    llm_output: dict[str, Any] = Field(default_factory=dict)

    def to_context_value(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "metadata": self.metadata,
            "llm_output": self.llm_output,
        }


class LoopScope(BaseModel):
    """Mirror of the innermost loop frame, exposed as the `loop` namespace."""

    item: Any = None
    index: int = 0
    count: int = 0
    item_var: str = "item"

    def to_context_value(self) -> dict[str, Any]:
        value = {"item": self.item, "index": self.index, "count": self.count}
        if self.item_var and self.item_var != "item":
            value[self.item_var] = self.item
        return value


class RunContext(BaseModel):
    """The persisted, typed execution context of a run."""

    start: TriggerContext | None = None
    loop: LoopScope | None = None
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # -- writers (called only by the run entity / stepper) -------------------

    def set_start(self, trigger: TriggerContext) -> None:
        self.start = trigger

    def record_node_output(self, node_id: str, output: dict[str, Any]) -> None:
        if node_id in RESERVED_NODE_IDS:
            raise ValueError(
                f"'{node_id}' is a reserved context namespace and cannot be a node id"
            )
        self.nodes[node_id] = output

    def set_loop_scope(self, scope: LoopScope | None) -> None:
        self.loop = scope

    # -- read view ------------------------------------------------------------

    def node_output(self, node_id: str) -> dict[str, Any] | None:
        return self.nodes.get(node_id)

    def to_view(self) -> dict[str, Any]:
        """Flat dict that JMESPath expressions resolve against."""
        view: dict[str, Any] = dict(self.nodes)
        if self.start is not None:
            view["start"] = self.start.to_context_value()
        if self.loop is not None:
            view["loop"] = self.loop.to_context_value()
        return view


def normalize_node_output(output: Any) -> dict[str, Any]:
    """Every node output is stored as a dict so `<node_id>.<field>` resolves."""
    if output is None:
        return {}
    if isinstance(output, Mapping):
        return dict(output)
    return {"result": output}


class ContextReader(Protocol):
    """Read-only resolution API handed to node executors."""

    def resolve(self, expression: str) -> Any: ...

    def resolve_required(self, expression: str) -> Any: ...

    def resolve_condition(self, expression: str) -> bool: ...

    def resolve_inputs(self, input_mapping: dict[str, InputBinding]) -> dict[str, Any]: ...


class RunContextReader:
    """ContextReader over a RunContext snapshot."""

    def __init__(self, context: RunContext):
        self._view = context.to_view()

    def resolve(self, expression: str) -> Any:
        return ExpressionEngine.evaluate(expression, self._view)

    def resolve_required(self, expression: str) -> Any:
        return ExpressionEngine.evaluate_required(expression, self._view)

    def resolve_condition(self, expression: str) -> bool:
        return ExpressionEngine.evaluate_condition(expression, self._view)

    def resolve_inputs(self, input_mapping: dict[str, InputBinding]) -> dict[str, Any]:
        """Resolve typed bindings. Required expressions that resolve to
        nothing raise ContextPathError naming the input key."""
        resolved: dict[str, Any] = {}
        for key, binding in input_mapping.items():
            if isinstance(binding, ExpressionInputBinding):
                if binding.optional:
                    resolved[key] = self.resolve(binding.value)
                else:
                    try:
                        resolved[key] = self.resolve_required(binding.value)
                    except ContextPathError as exc:
                        raise ContextPathError(
                            binding.value, detail=f"required by input '{key}'"
                        ) from exc
            else:
                resolved[key] = binding.value
        return resolved
