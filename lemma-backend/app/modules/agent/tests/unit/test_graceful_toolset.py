"""Unit tests for graceful tool-error handling (GracefulToolset + helpers)."""

from __future__ import annotations

import asyncio
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
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.graceful_toolset import GracefulToolset
from app.modules.agent.tools.tool_errors import (
    format_tool_error,
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


class _RaisingToolset:
    """A minimal toolset stand-in whose call_tool raises a given exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def call_tool(self, name, tool_args, ctx, tool):
        raise self._exc


@pytest.mark.anyio
async def test_graceful_toolset_returns_error_on_execution_failure():
    toolset = GracefulToolset(_RaisingToolset(RuntimeError("boom")))
    result = await toolset.call_tool("do_thing", {}, None, None)
    assert result == {
        "success": False,
        "error": "boom",
        "error_type": "RuntimeError",
        "tool": "do_thing",
    }


@pytest.mark.anyio
async def test_graceful_toolset_reraises_model_retry():
    toolset = GracefulToolset(_RaisingToolset(ModelRetry("try again")))
    with pytest.raises(ModelRetry):
        await toolset.call_tool("do_thing", {}, None, None)


@pytest.mark.anyio
async def test_graceful_toolset_reraises_cancellation():
    toolset = GracefulToolset(_RaisingToolset(asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await toolset.call_tool("do_thing", {}, None, None)


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
    toolset = GracefulToolset(_RaisingToolset(asyncio.CancelledError()))

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await toolset.call_tool("exec_command", {}, None, None)

    said = [r for r in caplog.records if "tool_cancelled_mid_flight" in r.getMessage()]
    assert said, "a cancelled tool call has to name itself"
    assert "exec_command" in said[0].getMessage()


@pytest.mark.anyio
async def test_failing_tool_does_not_abort_a_real_run():
    """A raising tool body becomes a tool response; the run still completes."""

    async def boom() -> str:
        raise RuntimeError("kaboom")

    toolset = GracefulToolset(FunctionToolset(tools=[boom]))

    calls = {"n": 0}

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="boom", args={})])
        return ModelResponse(parts=[TextPart(content="recovered")])

    agent = Agent(FunctionModel(model_fn), toolsets=[toolset], retries=5)
    result = await agent.run("go")

    assert result.output == "recovered"
    # The model saw the error as a tool return rather than the run crashing.
    rendered = "".join(
        str(getattr(part, "content", ""))
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
    )
    assert "kaboom" in rendered


class _ReturningToolset:
    """A toolset stand-in whose call_tool returns a given payload."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def call_tool(self, name, tool_args, ctx, tool):
        return self._payload


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
        await GracefulToolset(_ReturningToolset(payload)).call_tool(
            "pod_write_record", {}, None, None
        )

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
        await GracefulToolset(
            _ReturningToolset(_Response(error="the write never landed"))
        ).call_tool("exec_command", {}, None, None)

    (span,) = spans
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["lemma.outcome"] == "error"


@pytest.mark.anyio
async def test_a_successful_call_is_marked_ok():
    with _captured_spans() as spans:
        await GracefulToolset(_ReturningToolset({"success": True})).call_tool(
            "web_search", {}, None, None
        )

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
            await GracefulToolset(_RaisingToolset(ModelRetry("try again"))).call_tool(
                "ask_user", {}, None, None
            )

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
                await GracefulToolset(
                    _RaisingToolset(ModelRetry("try again"))
                ).call_tool("ask_user", {}, None, None)

    by_name = {span.name: span for span in spans}
    assert set(by_name) == {"lemma.agent.tool.ask_user", "running tool"}
    for name, span in by_name.items():
        assert span.status.status_code is not StatusCode.ERROR, (
            f"{name} still exports as a failed tool call"
        )
