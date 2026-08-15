"""Named spans for the fixed setup work an agent run does before the model.

Between the worker picking up ``process_agent_run`` and the first byte leaving
for the provider there is a flat run of roughly three dozen SQL spans. The
exporter strips statement text, so in a production trace that stretch is one
opaque block: measurable in total (p50 ~0.5s, p90 ~4.7s) and unattributable in
detail. These spans name the phases that issue those queries, so "the setup is
slow" can become "this phase is slow".

Deliberately one tracer with a hard-coded ``app.`` name: the span sanitizer
keeps a span's own name only for instrumentation scopes under ``app.``, and
rewrites everything else to a generic dependency name.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from opentelemetry import trace

from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType

_tracer = trace.get_tracer("app.modules.agent.run_phases")


def run_phase(name: str):
    """Span for one named phase of an agent run's pre-model setup.

    ``name`` must be lowercase dotted/underscored (``load_context``,
    ``tool_assembly``) — the sanitizer drops any other shape back to the
    generic dependency name.
    """
    return _tracer.start_as_current_span(f"lemma.agent.{name}")


def record_history_size(span, *, runs: Sequence[object], sent: Sequence[object]) -> None:
    """Record how much transcript a run read against how much it sent.

    Both numbers, because they diverge: the loader reads every run and every
    message of the conversation, and the window that actually reaches the model
    is chosen afterwards. A run that reads 900 messages to send 40 is invisible
    from either number alone.
    """
    span.set_attribute("lemma.history.runs_loaded", len(runs))
    span.set_attribute(
        "lemma.history.messages_loaded",
        sum(len(getattr(run, "messages", ())) for run in runs),
    )
    span.set_attribute("lemma.history.messages_sent", len(sent))


async def observe_first_output(
    events: AsyncIterator[AgentEvent],
) -> AsyncIterator[AgentEvent]:
    """Re-yield a harness's events, timing the run's first visible output.

    Time to first token is what the person waiting actually experiences, and
    nothing recorded it. The earliest timestamp any store held was the assistant
    message row, and that row is written when the message *finishes* — on a
    reasoning model, a whole thinking block later. In production the gap between
    the two is p50 4.3s, so every reading of "how long before the user sees
    something" was really a reading of how long the model spent thinking.

    Two spans, both opening when the harness starts: one closes on the first
    streamed token, the other on the first message worth persisting. A run that
    produces neither still exports both, tagged ``lemma.outcome=none``, so a
    silent run shows up as a long span rather than as no span at all.
    """
    token_span = _tracer.start_span("lemma.agent.first_token")
    message_span = _tracer.start_span("lemma.agent.first_message")
    pending = {"token": token_span, "message": message_span}
    try:
        async for event in events:
            key = "token" if event.type == AgentEventType.TOKEN else None
            if event.type == AgentEventType.MESSAGE:
                key = "message"
            span = pending.pop(key, None) if key else None
            if span is not None:
                span.set_attribute("lemma.outcome", "produced")
                span.end()
            yield event
    finally:
        for span in pending.values():
            span.set_attribute("lemma.outcome", "none")
            span.end()
