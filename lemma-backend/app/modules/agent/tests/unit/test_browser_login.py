from __future__ import annotations

from app.core.config import settings
from app.modules.agent.tools.browser import pydantic_adapter
from app.modules.agent.tools.browser.login import (
    BrowserLoginRequest,
    BrowserLoginResult,
    takeover_url,
)


def test_the_takeover_link_points_at_the_frontend_not_the_api() -> None:
    """It is a page a person opens in their own session, not an API call."""
    url = takeover_url("abc123")
    assert url.startswith(settings.frontend_url.rstrip("/"))
    assert url.endswith("/takeover/abc123")


def test_a_result_has_nowhere_to_put_a_credential() -> None:
    """The tool result reaches the model and the transcript."""
    fields = set(BrowserLoginResult.model_fields)
    assert not fields & {"password", "secret", "state", "username", "totp"}


def test_the_request_asks_for_a_site_not_a_credential() -> None:
    fields = set(BrowserLoginRequest.model_fields)
    assert "origin" in fields
    assert not fields & {"password", "username", "secret"}


def test_the_tool_tells_the_model_never_to_ask_for_a_password() -> None:
    """The prompt is the only thing standing between an agent and a plausible,
    disastrous shortcut."""
    doc = pydantic_adapter.browser_login.__doc__ or ""
    assert "never" in doc.lower()
    assert "password" in doc.lower()
    assert "takeover_url" in doc


def test_it_ships_with_the_other_browser_tools() -> None:
    names = {tool.__name__ for tool in pydantic_adapter.BROWSER_TOOLS}
    assert "browser_login" in names


def test_every_deferred_import_in_the_tool_actually_resolves() -> None:
    """These imports sit inside the function, so nothing loads them until an
    agent calls the tool — which is exactly how a missing module ships.

    This module was published to the wrong directory once, and the architecture
    checker did not notice because it reads import *statements* rather than
    following them.
    """
    import importlib
    import ast
    import pathlib

    source = pathlib.Path("app/modules/agent/tools/browser/login.py").read_text()
    deferred = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.col_offset > 0 and node.module
    }
    assert deferred, "expected the tool to defer some imports"
    for module in sorted(deferred):
        importlib.import_module(module)
