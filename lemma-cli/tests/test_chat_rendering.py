"""An agent run prints its ANSWER, not its interior.

A single `agents run` produced ~4KB of transcript for a three-sentence reply:
reasoning paragraphs with literal `{"tool_name": ...}` blobs inline, the real
answer buried in the last `final_result`. That is a token bill for every caller
and a parsing problem for anything driving the CLI.

The interior arrives as plain TOKENS, not as structured tool-call events, so
suppressing events is not enough — the answer has to be recovered from the token
text once the stream ends.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

from lemma_cli.cli_core.chat import (
    ChatRenderer,
    StreamEvent,
    extract_final_result,
    iter_sse_events,
    render_chat_stream,
)


_TRANSCRIPT = (
    "The user is asking about the refund window. I need to search "
    "/support-knowledge.\n"
    '{"tool_name":"pod_search_files","args":{"query": "refund window"}}'
    "I already have the contents. Let me synthesize.\n"
    '{"tool_name":"final_result","args":{"status": "COMPLETED", "output": '
    '{"answer": "Pro is 14 days.", "confident": true}}}'
)


def test_extract_final_result_finds_the_answer_in_a_token_transcript():
    assert extract_final_result(_TRANSCRIPT) == {
        "answer": "Pro is 14 days.",
        "confident": True,
    }


def test_extract_final_result_takes_the_LAST_call():
    text = (
        '{"tool_name":"final_result","args":{"output": "first"}}'
        "then it kept going\n"
        '{"tool_name":"final_result","args":{"output": "second"}}'
    )
    assert extract_final_result(text) == "second"


def test_extract_final_result_returns_none_without_a_marker():
    """No marker means the caller should print what it got, not swallow it."""
    assert extract_final_result("just a plain answer, no tool calls") is None


def test_extract_final_result_survives_braces_inside_strings():
    text = '{"tool_name":"final_result","args":{"output": "a } brace {"}}'
    assert extract_final_result(text) == "a } brace {"


def _render(tokens, *, verbose=False, capsys=None):
    renderer = ChatRenderer(agent="policy-lookup", verbose=verbose)
    for token in tokens:
        renderer.handle(StreamEvent(type="token", data=token))
    renderer.handle(StreamEvent(type="completed", data={"status": "COMPLETED"}))
    renderer.finish()
    return capsys.readouterr().out


def test_quiet_mode_prints_only_the_answer(capsys):
    out = _render([_TRANSCRIPT], capsys=capsys)
    assert "Pro is 14 days." in out
    # The interior is gone.
    assert "pod_search_files" not in out
    assert "Let me synthesize" not in out
    # And a bare "COMPLETED" doesn't precede the answer as if there were none.
    assert "COMPLETED" not in out


def test_verbose_mode_streams_everything(capsys):
    out = _render([_TRANSCRIPT], verbose=True, capsys=capsys)
    assert "pod_search_files" in out
    assert "Let me synthesize" in out
    assert "COMPLETED" in out


def test_quiet_mode_falls_back_to_the_raw_text(capsys):
    """An agent that just answers, with no final_result, must still be heard."""
    out = _render(["The refund window is 14 days."], capsys=capsys)
    assert "The refund window is 14 days." in out


def test_a_stopped_run_still_reports_its_status(capsys):
    """Suppressing "COMPLETED" must not also hide a run that did NOT complete."""
    renderer = ChatRenderer(agent="a", verbose=False)
    renderer.handle(StreamEvent(type="token", data="partial"))
    renderer.handle(StreamEvent(type="stopped", data={"status": "STOPPED"}))
    renderer.finish()
    assert "STOPPED" in capsys.readouterr().out


# --- token channels ----------------------------------------------------------


def _tokens(renderer: ChatRenderer, *payloads) -> str:
    for payload in payloads:
        renderer.handle(StreamEvent(type="TOKEN", data=payload, agent_run_id=None))
    return "".join(renderer.buffered)


def test_a_tool_delta_never_reaches_the_answer():
    """The bug, exactly as it was reported.

    The harness tags every delta — `text` is the answer, `thinking` is model
    reasoning, `tool` is the literal serialized call it streams so a UI can show
    a tool running. This renderer stringified the payload without reading the
    tag, so all three landed in the reply:

        I'll check the items table count.
        {"tool_name":"pod_query","args":{"sql":"SELECT COUNT(*) AS cnt FROM items"}}1
    """
    renderer = ChatRenderer(agent="pod agent")

    answer = _tokens(
        renderer,
        {"kind": "text", "data": "I'll check the items table count."},
        {"kind": "tool", "data": '{"tool_name":"pod_query","args":'},
        {"kind": "tool", "data": '{"sql":"SELECT COUNT(*) AS cnt FROM items"}}'},
        {"kind": "text", "data": " There is 1 row."},
    )

    assert answer == "I'll check the items table count. There is 1 row."
    assert "tool_name" not in answer
    assert "SELECT COUNT" not in answer


def test_reasoning_deltas_are_not_the_answer_either():
    renderer = ChatRenderer(agent="pod agent")

    answer = _tokens(
        renderer,
        {"kind": "thinking", "data": "The user wants a row count. I should query."},
        {"kind": "text", "data": "There is 1 row."},
    )

    assert answer == "There is 1 row."


def test_an_untagged_string_delta_still_renders():
    """Not every runtime sends the envelope.

    Dropping their output would trade a cosmetic bug for a silent one — the
    reply would simply be empty.
    """
    renderer = ChatRenderer(agent="pod agent")

    assert _tokens(renderer, "plain text from an older runtime") == (
        "plain text from an older runtime"
    )


def test_a_payload_with_no_kind_is_treated_as_text():
    renderer = ChatRenderer(agent="pod agent")

    assert _tokens(renderer, {"data": "no kind here"}) != ""


def test_verbose_shows_the_answer_channel_only():
    """--verbose is for watching a run, not for leaking the tool envelope."""
    renderer = ChatRenderer(agent="pod agent", verbose=True)
    renderer.handle(
        StreamEvent(
            type="TOKEN",
            data={"kind": "tool", "data": '{"tool_name":"x"}'},
            agent_run_id=None,
        )
    )

    assert renderer.printed_tokens is False, "a tool delta is not the answer"


# --- the wire, not a hand-built shape ----------------------------------------


class _SseResponse:
    """A response that yields exactly the bytes the server sends."""

    def __init__(self, *frames: str) -> None:
        self._frames = frames

    def iter_lines(self, decode_unicode: bool = False):
        for frame in self._frames:
            yield f"data: {frame}"
            yield ""


def test_a_tool_delta_never_reaches_the_answer_over_the_real_wire():
    """The same assertion as above, driven through the actual SSE parser.

    The tests above construct ``data={"kind": "tool", ...}`` — a nested shape
    ``iter_sse_events`` can never produce. The server puts ``kind`` *beside*
    ``data`` and makes ``data`` a bare string, pinned by the backend's own
    ``test_sse_frames.py``. So those tests passed against a fabricated envelope
    while every real tool call went on reaching users:

        hello
        {"tool_name":"pod_query","args":{"sql":"SELECT COUNT(*) AS count FROM items"}}332

    Nothing here may hand-build a StreamEvent. That is the whole point.
    """
    renderer = ChatRenderer(agent="pod agent")
    response = _SseResponse(
        '{"type":"token","data":"hello","kind":"text"}',
        '{"type":"token","data":"{\\"tool_name\\":\\"pod_query\\",\\"args\\":","kind":"tool"}',
        '{"type":"token","data":"{\\"sql\\":\\"SELECT COUNT(*) AS count FROM items\\"}}","kind":"tool"}',
        '{"type":"token","data":" There are 332.","kind":"text"}',
    )

    for event in iter_sse_events(response):
        renderer.handle(event)
    answer = "".join(renderer.buffered)

    assert answer == "hello There are 332."
    assert "tool_name" not in answer
    assert "SELECT COUNT" not in answer


def test_the_parser_carries_the_kind_tag_off_the_frame():
    """`kind` is a sibling of `data`; reading it from inside `data` finds
    nothing, which is why the filter that existed never once fired."""
    response = _SseResponse('{"type":"token","data":"hi","kind":"thinking"}')

    event = next(iter(iter_sse_events(response)))

    assert event.kind == "thinking"
    assert event.data == "hi", "data is a bare string on the wire, never a dict"


def test_a_thinking_delta_over_the_wire_is_not_the_answer():
    renderer = ChatRenderer(agent="pod agent")
    response = _SseResponse(
        '{"type":"token","data":"The user wants a count. I should query.","kind":"thinking"}',
        '{"type":"token","data":"There is 1 row.","kind":"text"}',
    )

    for event in iter_sse_events(response):
        renderer.handle(event)

    assert "".join(renderer.buffered) == "There is 1 row."


class _Stream:
    """A stubbed SSE response yielding the given event payloads."""

    def __init__(self, *payloads: str) -> None:
        self._payloads = payloads
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for payload in self._payloads:
            yield f"data: {payload}"
            yield ""

    def close(self):
        self.closed = True


def _run_stream(*payloads: str, output: str = "pretty"):
    """Render a stream and return the typer.Exit it raised, or None."""
    state = SimpleNamespace(output=output)
    try:
        render_chat_stream(state=state, response=_Stream(*payloads), agent="a")
    except typer.Exit as exit_:
        return exit_
    return None


def test_a_failed_run_exits_non_zero():
    """A server-side failure used to print red text and exit 0.

    Every script, CI step and agent driving this CLI therefore read a failed run
    as a successful one — the defect the ordinary request path had already fixed
    by funnelling through `fail`.
    """
    exit_ = _run_stream(
        '{"type":"token","data":"working on it"}',
        '{"type":"error","data":"model provider returned 500"}',
    )
    assert exit_ is not None and exit_.exit_code == 1


def test_a_stream_that_never_reports_an_outcome_exits_non_zero():
    """A dropped connection mid-run is not a success.

    The server always ends a stream with a terminal event — the run pump
    force-fails one that does not — so its absence means the connection went
    away and the outcome is genuinely unknown.
    """
    exit_ = _run_stream('{"type":"token","data":"half an ans"}')
    assert exit_ is not None and exit_.exit_code == 1


def test_a_completed_run_exits_zero():
    assert (
        _run_stream(
            '{"type":"token","data":"done"}',
            '{"type":"completed","data":{"status":"completed"}}',
        )
        is None
    )


def test_a_run_the_person_stopped_is_not_a_failure():
    """Stopping a run is a thing the user chose, not an error to report."""
    assert (
        _run_stream(
            '{"type":"token","data":"partial"}',
            '{"type":"completed","data":{"status":"stopped"}}',
        )
        is None
    )


def test_the_json_path_has_the_same_exit_code(capsys):
    """A machine reading `--output json` has the same right to the exit code."""
    exit_ = _run_stream(
        '{"type":"error","data":"boom"}',
        output="json",
    )
    assert exit_ is not None and exit_.exit_code == 1
    # The transcript still reaches the consumer before the process exits.
    assert "boom" in capsys.readouterr().out


def test_attaching_to_someone_elses_run_does_not_fail_on_a_quiet_stream():
    """`conversations stream` is an observer, not the run's owner.

    Exiting non-zero here would make "I watched for a while and detached"
    indistinguishable from "the run broke".
    """
    state = SimpleNamespace(output="pretty")
    render_chat_stream(
        state=state,
        response=_Stream('{"type":"token","data":"attached"}'),
        agent=None,
        owns_run=False,
    )


def test_an_observer_still_reports_an_error_the_run_reported():
    """That one is about the run, not about who is watching."""
    state = SimpleNamespace(output="pretty")
    with pytest.raises(typer.Exit) as caught:
        render_chat_stream(
            state=state,
            response=_Stream('{"type":"error","data":"provider 500"}'),
            agent=None,
            owns_run=False,
        )
    assert caught.value.exit_code == 1
