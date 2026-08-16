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

from lemma_cli.cli_core.chat import ChatRenderer, extract_final_result


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
        renderer.handle(SimpleNamespace(type="token", data=token))
    renderer.handle(SimpleNamespace(type="completed", data={"status": "COMPLETED"}))
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
    renderer.handle(SimpleNamespace(type="token", data="partial"))
    renderer.handle(SimpleNamespace(type="stopped", data={"status": "STOPPED"}))
    renderer.finish()
    assert "STOPPED" in capsys.readouterr().out


# --- token channels ----------------------------------------------------------


def _tokens(renderer: ChatRenderer, *payloads) -> str:
    for payload in payloads:
        renderer.handle(SimpleNamespace(type="TOKEN", data=payload, agent_run_id=None))
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
        SimpleNamespace(
            type="TOKEN", data={"kind": "tool", "data": '{"tool_name":"x"}'}, agent_run_id=None
        )
    )

    assert renderer.printed_tokens is False, "a tool delta is not the answer"
