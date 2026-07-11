"""Typed run context: write rules, strict resolution, loop scoping."""

import pytest

from app.modules.workflow.domain.context import (
    LoopScope,
    RunContext,
    RunContextReader,
    TriggerContext,
    normalize_node_output,
)
from app.modules.workflow.domain.errors import ContextPathError
from app.modules.workflow.domain.nodes import (
    ExpressionInputBinding,
    LiteralInputBinding,
)
from app.modules.workflow.domain.start import WorkflowStartType


def test_reserved_namespaces_rejected_as_node_ids():
    context = RunContext()
    for reserved in ("start", "loop"):
        with pytest.raises(ValueError, match="reserved"):
            context.record_node_output(reserved, {})


def test_manual_run_has_no_start_namespace():
    context = RunContext()
    context.record_node_output("intake", {"amount": 12})
    view = context.to_view()
    assert "start" not in view
    assert view["intake"] == {"amount": 12}


def test_trigger_populates_start_namespace_only():
    context = RunContext()
    context.set_start(
        TriggerContext(
            trigger_type=WorkflowStartType.DATASTORE_EVENT,
            payload={"record": {"id": 1}},
            metadata={"table_name": "users"},
        )
    )
    view = context.to_view()
    assert view["start"]["payload"] == {"record": {"id": 1}}
    assert view["start"]["metadata"] == {"table_name": "users"}
    assert view["start"]["llm_output"] == {}
    # No root-level merging, ever.
    assert "payload" not in view
    assert "record" not in view


def test_loop_scope_exposes_item_index_count_and_alias():
    context = RunContext()
    context.set_loop_scope(
        LoopScope(item={"merchant": "Uber"}, index=1, count=3, item_var="line")
    )
    view = context.to_view()
    assert view["loop"]["item"] == {"merchant": "Uber"}
    assert view["loop"]["line"] == {"merchant": "Uber"}
    assert view["loop"]["index"] == 1
    assert view["loop"]["count"] == 3
    context.set_loop_scope(None)
    assert "loop" not in context.to_view()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, {}),
        ({"a": 1}, {"a": 1}),
        ([1, 2], {"result": [1, 2]}),
        ("text", {"result": "text"}),
        (42, {"result": 42}),
    ],
)
def test_node_outputs_normalized_to_dicts(raw, expected):
    assert normalize_node_output(raw) == expected


def test_reader_strict_resolution_raises_with_path():
    context = RunContext()
    context.record_node_output("intake", {"amount": 10})
    reader = RunContextReader(context)
    assert reader.resolve("intake.amount") == 10
    assert reader.resolve("intake.missing") is None
    with pytest.raises(ContextPathError, match="intake.missing"):
        reader.resolve_required("intake.missing")


def test_reader_resolve_inputs_required_vs_optional_vs_literal():
    context = RunContext()
    context.record_node_output("intake", {"amount": 10})
    reader = RunContextReader(context)

    resolved = reader.resolve_inputs(
        {
            "amount": ExpressionInputBinding(value="intake.amount"),
            "memo": ExpressionInputBinding(value="intake.memo", optional=True),
            "currency": LiteralInputBinding(value="USD"),
        }
    )
    assert resolved == {"amount": 10, "memo": None, "currency": "USD"}

    with pytest.raises(ContextPathError, match="required by input 'merchant'"):
        reader.resolve_inputs(
            {"merchant": ExpressionInputBinding(value="intake.merchant")}
        )


def test_round_trip_serialization():
    context = RunContext()
    context.set_start(TriggerContext(payload={"a": 1}))
    context.record_node_output("step", {"out": True})
    restored = RunContext.model_validate(context.model_dump(mode="json"))
    assert restored.to_view()["step"] == {"out": True}
    assert restored.to_view()["start"]["payload"] == {"a": 1}
