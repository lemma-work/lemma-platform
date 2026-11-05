"""The tool runtime: a raising tool body never ends a run, and every call is traced."""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import contextmanager

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from uuid import uuid4

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.tools import ToolGroup, ToolPolicy, ToolSpec
from app.modules.agent.infrastructure.harness.lemma.toolset_adapter import (
    function_toolset,
)
from app.modules.agent.infrastructure.tools.pod import tools as pod_tools
from app.modules.agent.infrastructure.tools.tool_errors import format_tool_error
from app.modules.agent.services.tool_runtime import (
    WITHHELD_MESSAGE,
    ToolRuntime,
    UnknownToolError,
)
from app.modules.agent.infrastructure.providers.control_flow import (
    is_control_flow_exception,
)


def test_format_tool_error_shape():
    err = format_tool_error("mytool", RuntimeError("nope"))
    assert err == {
        "success": False,
        "error": "nope",
        "error_type": "RuntimeError",
        "tool": "mytool",
    }


def test_is_control_flow_exception():
    assert is_control_flow_exception(ModelRetry("x"))
    assert is_control_flow_exception(asyncio.CancelledError())
    assert is_control_flow_exception(KeyboardInterrupt())
    assert not is_control_flow_exception(RuntimeError("x"))
    assert not is_control_flow_exception(ValueError("x"))


def _ctx(**fields: object) -> ToolContext:
    return ToolContext(
        user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4(), **fields
    )


def _spec(name: str, *, raises: BaseException | None = None, returns=None) -> ToolSpec:
    async def run(ctx: ToolContext, request: object) -> object:
        if raises is not None:
            raise raises
        return returns

    return ToolSpec(
        name=name,
        description="",
        parameters=None,
        json_schema={"type": "object"},
        toolset=ToolGroup.CALLABLES,
        run=run,
    )


async def _call(spec: ToolSpec) -> object:
    return await ToolRuntime([spec]).call(spec.name, {}, _ctx())


@pytest.mark.anyio
async def test_a_raising_tool_returns_its_error():
    result = await _call(_spec("do_thing", raises=RuntimeError("boom")))
    assert result == {
        "success": False,
        "error": "boom",
        "error_type": "RuntimeError",
        "tool": "do_thing",
    }


@pytest.mark.anyio
async def test_a_retry_is_reraised():
    with pytest.raises(ModelRetry):
        await _call(_spec("do_thing", raises=ModelRetry("try again")))


@pytest.mark.anyio
async def test_a_cancellation_is_reraised():
    with pytest.raises(asyncio.CancelledError):
        await _call(_spec("do_thing", raises=asyncio.CancelledError()))


@pytest.mark.anyio
async def test_a_cancelled_tool_call_says_which_tool_it_was(caplog):
    """The one fact the cancellation logs did not carry.

    `reraise_driver_failure` already separates who asked for a cancellation --
    its two counters say whether it came from outside or was aimed at the
    driver alone -- but the frames it captures are all harness, the deepest
    being whichever poll loop the task happened to be suspended in. Four
    cancelled runs in dev were diagnosable down to "something inside the graph"
    and no further. This boundary is the only place that knows the tool's name.
    """
    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await _call(_spec("exec_command", raises=asyncio.CancelledError()))

    said = [r for r in caplog.records if "tool_cancelled_mid_flight" in r.getMessage()]
    assert said, "a cancelled tool call has to name itself"
    assert "exec_command" in said[0].getMessage()


@pytest.mark.anyio
async def test_failing_tool_does_not_abort_a_real_run():
    """A raising tool body becomes a tool response; the run still completes."""

    spec = _spec("boom", raises=RuntimeError("kaboom"))
    toolset = function_toolset([spec], ToolRuntime([spec]))

    calls = {"n": 0}

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="boom", args={})])
        return ModelResponse(parts=[TextPart(content="recovered")])

    agent = Agent(
        FunctionModel(model_fn), toolsets=[toolset], retries=5, deps_type=ToolContext
    )
    result = await agent.run("go", deps=_ctx())

    assert result.output == "recovered"
    # The model saw the error as a tool return rather than the run crashing.
    rendered = "".join(
        str(getattr(part, "content", ""))
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
    )
    assert "kaboom" in rendered


def _span_collector():
    """Collect exported spans without standing a double inside the subject.

    `run_phase` holds a module-level tracer, but it is a *ProxyTracer*: it
    resolves lazily to whatever global provider is installed. So attaching a
    real SDK provider is enough, and nothing in `app/` is patched — the gate on
    in-subject doubles is right that patching there proves less.

    A real provider rather than a mock because part of what is under test is the
    span context manager's own behaviour: that setting OK before re-raising
    stops it stamping ERROR on the way out. A mock would assert our belief about
    that rather than the fact.
    """
    exported: list[ReadableSpan] = []

    class _Collect(SpanExporter):
        def export(self, spans):
            exported.extend(spans)

        def shutdown(self):
            return None

    processor = SimpleSpanProcessor(_Collect())
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        # Somebody already installed one; a second `set_tracer_provider` is
        # ignored by OTel, so join theirs instead of quietly collecting nothing.
        provider.add_span_processor(processor)
    else:
        provider = TracerProvider()
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
    return exported


@contextmanager
def _captured_spans():
    """The spans exported while the block runs."""
    exported = _span_collector()
    before = len(exported)
    yield exported
    del exported[:before]


@pytest.mark.anyio
async def test_a_returned_failure_marks_its_span():
    """The failure that hides: the tool returns, so nothing raised.

    Before this the span ended UNSET and looked exactly like a success, which
    is why a measured tool-error rate read less than half the real one.
    """
    payload = {"success": False, "error": "no grant", "code": "FORBIDDEN"}
    with _captured_spans() as spans:
        await _call(_spec("pod_write_record", returns=payload))

    (span,) = spans
    assert span.name == "lemma.agent.tool.pod_write_record"
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "no grant"
    assert span.attributes["lemma.outcome"] == "error"
    assert span.attributes["error.code"] == "FORBIDDEN"


@pytest.mark.anyio
async def test_a_returned_failure_is_seen_through_a_pydantic_response():
    """Most tools return a model, not a dict — reading only dicts missed them."""

    class _Response(BaseModel):
        success: bool = False
        error: str | None = None

    with _captured_spans() as spans:
        await _call(
            _spec("exec_command", returns=_Response(error="the write never landed"))
        )

    (span,) = spans
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["lemma.outcome"] == "error"


@pytest.mark.anyio
async def test_a_successful_call_is_marked_ok():
    with _captured_spans() as spans:
        await _call(_spec("web_search", returns={"success": True}))

    (span,) = spans
    assert span.status.status_code is not StatusCode.ERROR
    assert span.attributes["lemma.outcome"] == "ok"


@pytest.mark.anyio
async def test_a_pause_is_not_an_error():
    """`ask_user` and `wait_for` end their turn by raising. That is not a fault.

    The span context manager stamps ERROR on any exception that leaves it, so
    without the explicit OK every pause would export as a failed tool call —
    the exact opposite of making real failures legible.
    """
    with _captured_spans() as spans:
        with pytest.raises(ModelRetry):
            await _call(_spec("ask_user", raises=ModelRetry("try again")))

    (span,) = spans
    assert span.status.status_code is not StatusCode.ERROR
    assert span.attributes["lemma.outcome"] == "control_flow"


@pytest.mark.anyio
async def test_a_pause_is_not_an_error_on_the_span_the_llm_backend_sees():
    """Marking our own span was not enough, and the traces showed it.

    There are two spans per tool call and they go to different places. The
    run-phase span is ours and is exported to the infrastructure pipeline; the
    span the harness opens around the call is the one carrying an OpenInference
    kind, and only spans with one of those are forwarded to the LLM backend.
    So the OK went to the pipeline nobody reads error rates in, and in the one
    they do, `ask_user`, `request_approval` and `browser_sign_in` -- the three
    tools that exist to stop and ask a person -- made up almost every recorded
    tool error.

    The enclosing span here stands in for the harness's. `StatusCode.OK` is
    final in the SDK, so what this really pins is that the mark survives the
    exception unwinding back out through it.
    """
    tracer = trace.get_tracer("app.tests.caller")
    with _captured_spans() as spans:
        with pytest.raises(ModelRetry):
            with tracer.start_as_current_span("running tool"):
                await _call(_spec("ask_user", raises=ModelRetry("try again")))

    by_name = {span.name: span for span in spans}
    assert set(by_name) == {"lemma.agent.tool.ask_user", "running tool"}
    for name, span in by_name.items():
        assert span.status.status_code is not StatusCode.ERROR, (
            f"{name} still exports as a failed tool call"
        )


@pytest.mark.anyio
async def test_an_unknown_tool_is_refused_before_anything_runs():
    with pytest.raises(UnknownToolError):
        await ToolRuntime([]).call("pod_tables", {}, _ctx())


@pytest.mark.anyio
async def test_a_strangers_run_is_refused_a_tool_its_policy_withholds():
    """The last word on a stranger's run, whatever list the caller built."""
    spec = _spec("exec_command", returns={"success": True})
    stranger = _ctx(audience=Audience.contact(uuid4()))

    assert await ToolRuntime([spec]).call(spec.name, {}, stranger) == {
        "success": False,
        "error": WITHHELD_MESSAGE,
    }
    allowed = dataclasses.replace(spec, policy=ToolPolicy(outsider_allowed=True))
    assert await ToolRuntime([allowed]).call(spec.name, {}, stranger) == {
        "success": True
    }


def test_arguments_validate_flat_or_wrapped_with_the_tools_own_title():
    """As pydantic-ai validates them: the model's fields at the top level, or
    wrapped under the argument's name, and an error titled by the tool."""
    spec = next(spec for spec in pod_tools.SPECS if spec.name == "pod_get_records")

    flat = spec.parse({"table_name": "orders"})
    wrapped = spec.parse({"request": {"table_name": "orders"}})

    assert flat == wrapped
    with pytest.raises(Exception, match="1 validation error for pod_get_records"):
        spec.parse({"table_name": 3})


@pytest.mark.anyio
async def test_a_call_carries_its_id_and_the_runtime_to_the_tool():
    seen: dict[str, object] = {}

    async def run(ctx: ToolContext, request: object) -> object:
        seen.update(call=ctx.tool_call_id, tools=ctx.tools)
        return {}

    spec = ToolSpec(
        name="probe",
        description="",
        parameters=None,
        json_schema={"type": "object"},
        toolset=ToolGroup.CALLABLES,
        run=run,
    )
    runtime = ToolRuntime([spec])

    await runtime.call("probe", {}, _ctx(), tool_call_id="call-1")

    assert seen == {"call": "call-1", "tools": runtime}
