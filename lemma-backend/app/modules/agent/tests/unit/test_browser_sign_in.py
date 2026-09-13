"""The sign-in tool pauses the run, and the person's answer resumes it.

The previous version returned a link and told the model to wait. Nothing
resumed the run, so the agent's only move was to end its turn and hope somebody
mentioned it later. These assert the parts that make waiting real.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.pausing_tools import (
    PAUSING_TOOL_NAMES,
    USER_PAUSING_TOOL_NAMES,
)
from app.modules.agent.tools.browser.models import (
    BrowserSignInRequest,
    BrowserSignInResponse,
)
from app.modules.agent.tools.browser.sign_in import (
    SIGN_IN_TOOL_NAME,
    sign_in_internal,
)
from app.modules.agent.tools.tool_errors import (
    AgentInputRequired,
    is_control_flow_exception,
)


def test_the_tool_is_registered_as_one_a_person_resolves() -> None:
    """Without this, history reconstruction reports the pending call as a failed
    tool and the approvals list never shows it, so nobody can answer."""
    assert SIGN_IN_TOOL_NAME in USER_PAUSING_TOOL_NAMES
    assert SIGN_IN_TOOL_NAME in PAUSING_TOOL_NAMES


def test_the_pause_is_control_flow_and_must_not_be_swallowed() -> None:
    """A graceful tool boundary that caught this would turn the pause into a
    tool error, and the run would carry on as if nobody had been asked."""
    assert is_control_flow_exception(AgentInputRequired("call-1", SIGN_IN_TOOL_NAME))


class _Deps:
    conversation_id = uuid4()


class _Service:
    def __init__(self, *, loaded: bool, detail: str = "") -> None:
        self._loaded = loaded
        self._detail = detail
        self.requests: list[dict] = []
        self.closed = False

    async def try_saved_login(self, *, origin, auth_ctx=None):
        return self._loaded, self._detail

    async def open_request(self, **kwargs):
        self.requests.append(kwargs)
        return

    async def close(self):
        self.closed = True


@pytest.fixture
def patched(monkeypatch):
    def _install(service):
        # Patched where the tool looks it up -- through the published
        # contract, which is the only way another module may reach it.
        monkeypatch.setattr(
            "app.modules.web_login.contracts.SignInService",
            lambda *_a, **_k: service,
        )
        monkeypatch.setattr("app.core.api.dependencies.get_uow_factory", object)

    return _install


async def test_a_working_saved_login_does_not_ask_anybody(patched) -> None:
    service = _Service(loaded=True, detail="signed in with a saved login")
    patched(service)

    result = await sign_in_internal(
        _Deps(),
        BrowserSignInRequest(origin="app.example.com", reason="pull invoices"),
        tool_call_id="call-1",
    )
    assert isinstance(result, BrowserSignInResponse)
    assert result.outcome == "signed_in"
    assert result.source == "saved"
    assert service.requests == [], "nobody was asked"
    assert service.closed


async def test_no_saved_login_pauses_the_run(patched) -> None:
    service = _Service(loaded=False, detail="no saved login for this site")
    patched(service)

    with pytest.raises(AgentInputRequired) as raised:
        await sign_in_internal(
            _Deps(),
            BrowserSignInRequest(origin="app.example.com", reason="pull invoices"),
            tool_call_id="call-7",
        )

    assert raised.value.tool_call_id == "call-7"
    assert raised.value.kind == SIGN_IN_TOOL_NAME
    assert len(service.requests) == 1, "the person has something to answer"
    assert service.requests[0]["origin"] == "https://app.example.com"
    assert service.requests[0]["tool_call_id"] == "call-7"


async def test_without_a_durable_call_id_it_refuses_rather_than_stranding(
    patched,
) -> None:
    """Pausing on a call nothing can resolve would leave the person's decision
    with nowhere to land."""
    service = _Service(loaded=False)
    patched(service)

    result = await sign_in_internal(
        _Deps(),
        BrowserSignInRequest(origin="app.example.com", reason="x"),
        tool_call_id=None,
    )
    assert result.success is False
    assert result.outcome == "error"


async def test_a_bad_origin_is_answered_not_raised(patched) -> None:
    service = _Service(loaded=False)
    patched(service)

    result = await sign_in_internal(
        _Deps(),
        BrowserSignInRequest(origin="not a url at all", reason="x"),
        tool_call_id="call-2",
    )
    assert result.success is False
    assert service.requests == []


def test_the_tool_never_offers_a_place_to_put_a_password() -> None:
    """`connectors-and-accounts.md`: the system shall never ask a person for
    their provider password. A field for one is how that starts."""
    fields = set(BrowserSignInRequest.model_fields)
    assert fields == {"origin", "reason"}
    for banned in ("password", "username", "secret", "totp", "credential"):
        assert not any(banned in f for f in fields)


def test_the_response_cannot_carry_a_secret_either() -> None:
    fields = set(BrowserSignInResponse.model_fields)
    for banned in ("password", "cookie", "token", "secret", "state"):
        assert not any(banned in f for f in fields), banned
