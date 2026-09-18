"""The agent browses with `agent-browser`, and that is the only way it browses.

There used to be five typed tools beside `browser_sign_in` -- `browser_open`,
`browser_snapshot`, `browser_act`, `browser_read`, `browser_screenshot`. Every
one of them built a single `agent-browser` command, ran it through the same
shell session the agent can already reach, and parsed the same JSON back. Two
ways to do one thing, a skill that had to keep saying which to prefer, and a
round trip per step where the command line chains with `&&`.

What the wrappers were quietly providing had to move rather than be dropped,
and that is what this file guards:

* the conversation's own browser, which the *shell's environment* now names for
  every command rather than each tool naming it per call; and
* the human-takeover lease, which now lives in the `lemma-node-tool` wrapper --
  see `sandbox_runtime/tests/test_agent_browser_bootstrap.py`, because it has
  to hold for a command the agent typed itself, which is precisely what the
  typed tools never saw.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.tools.browser.pydantic_adapter import (
    BROWSER_TOOLS,
    browser_toolset,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    _browser_session_env,
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


def test_a_conversations_shell_is_put_in_its_own_browser() -> None:
    """Now that the CLI is the whole surface, this is the *only* thing that
    keeps two conversations out of each other's browser. The typed tools used
    to name the session on every command they built; a command the agent types
    itself inherits it from the shell or not at all."""
    first = _browser_session_env(f"conv-{uuid4().hex}")
    second = _browser_session_env(f"conv-{uuid4().hex}")

    assert first["AGENT_BROWSER_SESSION"] != second["AGENT_BROWSER_SESSION"]
    # Both names or neither. `agent-browser` points every session at the
    # image's single profile directory unless told otherwise, and the second
    # browser to open a profile Chrome has locked exits at once, reporting only
    # "Chrome exited early".
    assert first["AGENT_BROWSER_PROFILE"] != second["AGENT_BROWSER_PROFILE"]


def test_no_session_leaves_the_images_own_defaults_alone() -> None:
    """A shell with no conversation behind it must not be handed an empty
    session name -- that is not the same as saying nothing, and it would point
    the browser at a profile path built from nothing."""
    assert _browser_session_env(None) == {}
    assert _browser_session_env("") == {}
