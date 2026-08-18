"""The model is never told to poll a process with an empty string.

The tool sweep reported `manage_process(..., chars="")` failing as malformed
JSON. It did -- rejected by the agent client, in the model's own token stream,
before Lemma saw anything; nothing here strips empty strings. But `chars=""` and
an omitted `chars` reach identical code (`if chars:` in the sandbox session), so
the idiom bought nothing and cost a JSON-emission hazard on the single
most-repeated call in the system prompt.

Guarded as text because it *is* text: this lives in a prompt fragment and in
tool descriptions, where nothing else would notice it coming back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"

_MODEL_FACING = [
    _PROMPTS / "workspace_cli.md",
    _TOOLS / "workspace_cli" / "workspace_cli.py",
    _TOOLS / "workspace_cli" / "pydantic_adapter.py",
    _TOOLS / "workspace_cli" / "models.py",
]


@pytest.mark.parametrize("path", _MODEL_FACING, ids=lambda p: p.name)
def test_no_model_facing_text_teaches_an_empty_chars_argument(path: Path):
    assert path.exists(), path
    text = path.read_text(encoding="utf-8")
    for needle in ('chars=""', "chars=''"):
        assert needle not in text, (
            f"{path.name} tells the model to pass {needle}. Omit `chars` "
            "instead: it reaches the same code and does not invite the model "
            "to emit an empty-string argument."
        )
    # The kwarg spelling is only how *prose* writes it. A field description
    # names the field in its own sentence and shows the value alone, as in
    # 'Stdin for action="input"; `""` polls output' -- which is what survived
    # the first pass of this rule, in the two highest-leverage places there
    # are: the `chars` descriptions that ship inside the tool's JSON schema,
    # attached to the argument the model is filling in.
    for line in text.splitlines():
        if '`""`' not in line and "'\"\"'" not in line:
            continue
        assert "poll" not in line.lower(), (
            f"{path.name} still offers an empty string as the way to poll:\n"
            f"    {line.strip()}\n"
            "Say to omit the field instead."
        )


def test_omitting_chars_is_the_documented_way_to_poll():
    """The replacement is present, so the guidance was rewritten rather than
    merely deleted -- an agent still has to learn how to poll."""
    prompt = (_PROMPTS / "workspace_cli.md").read_text(encoding="utf-8")
    assert 'manage_process(action="input", process_id="<id>")' in prompt


def test_an_omitted_chars_still_polls_rather_than_writing_stdin():
    """The behaviour the guidance now relies on: no chars means read, not write.

    If this ever flipped, the documented poll would start typing an empty line
    into somebody's shell.
    """
    source = (
        Path(__file__).resolve().parents[3] / "workspace" / "sandbox_session.py"
    ).read_text(encoding="utf-8")
    assert "if chars:" in source


def test_the_agent_host_prompt_teaches_both_ways_of_waiting():
    """All three pausing tools work here now, and they do not work alike.

    They used to return `interaction_fallback: true` and the prompt said so.
    Then `ask_user`/`request_approval` learned to hold their MCP response open
    while a person decides, and `snooze` learned to end the turn and be woken --
    two different contracts, neither of them a fallback. A prompt still
    describing the old refusal is worse than one saying nothing: an agent that
    believes asking is unavailable will not ask.
    """
    from app.modules.agent.domain.prompts import load_agent_host_runtime_prompt

    prompt = load_agent_host_runtime_prompt()

    for tool in ("ask_user", "request_approval", "snooze"):
        assert tool in prompt, f"{tool} works here and goes unmentioned"
    # Nothing may still describe them as refusing, or as unable to pause. Both
    # sentences were true once and are now the opposite of the behaviour.
    assert "interaction_fallback" not in prompt
    assert "cannot suspend a turn" not in prompt
    # The two contracts are different in the one way that changes what the agent
    # should do, so the prompt has to distinguish them.
    assert "keep you in this turn" in prompt
    assert "ends this turn" in prompt
