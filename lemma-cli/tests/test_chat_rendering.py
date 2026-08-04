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
