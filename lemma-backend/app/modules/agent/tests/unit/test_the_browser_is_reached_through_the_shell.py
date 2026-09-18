"""The agent browses with `agent-browser`, and that is the only way it browses.

There used to be five typed tools beside `browser_sign_in` -- `browser_open`,
`browser_snapshot`, `browser_act`, `browser_read`, `browser_screenshot`. Every
one of them built a single `agent-browser` command, ran it through the same
shell session the agent can already reach, and parsed the same JSON back. Two
ways to do one thing, a skill that had to keep saying which to prefer, and a
round trip per step where the command line chains with `&&`.

What the wrappers were quietly providing had to move rather than be dropped,
and that is what this file guards:

* the human-takeover lease, which now lives in the `lemma-node-tool` wrapper --
  see `sandbox_runtime/tests/test_agent_browser_bootstrap.py`, because it has
  to hold for a command the agent typed itself, which is precisely what the
  typed tools never saw.

The other thing it used to guard is gone on purpose. Each conversation had a
browser of its own, named into the shell's environment, and a sign-in
therefore had to be captured in one browser and rebuilt in another -- which
meant guessing which cookies were the login, and guessing wrong. One durable
profile per person removed the carrying and the guess together, so there is no
per-conversation name left to assert.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.tools.browser.pydantic_adapter import (
    BROWSER_TOOLS,
    browser_toolset,
)
pytestmark = pytest.mark.unit


def test_signing_in_is_the_only_typed_browser_tool() -> None:
    """Anything else would be a second way to do what the command line does."""
    assert [tool.__name__ for tool in BROWSER_TOOLS] == ["browser_sign_in"]
    assert set(browser_toolset.tools) == {"browser_sign_in"}


def test_the_sign_in_tool_still_says_it_never_wants_a_password() -> None:
    """The docstring is the prompt. It is the only thing standing between a
    model and a login form it thinks it should fill in, so it is asserted
    rather than left to survive an edit by luck."""
    doc = browser_toolset.tools["browser_sign_in"].function.__doc__ or ""
    assert "without ever seeing or asking for a password" in doc
    assert "never put one in a command" in doc


def test_the_shell_is_left_in_the_images_own_browser() -> None:
    """Nothing overrides the browser a shell lands in, and that is the fix.

    This used to export a per-conversation `AGENT_BROWSER_SESSION` and a
    profile path to match, because each conversation had a Chrome of its own.
    There is one now, it keeps its profile in the durable home, and the image
    names it -- so a command the agent types, the relay, and the pane a person
    is watching all reach the same browser without anyone passing a name.
    """
    from app.modules.workspace.services import workspace_sandbox_service as module

    assert not hasattr(module, "_browser_session_env"), (
        "a per-conversation browser name is what the durable profile removed"
    )

    from app.modules.workspace.domain import browser_context

    assert not hasattr(browser_context, "agent_session")
    assert browser_context.DEFAULT_SESSION == "workspace"
