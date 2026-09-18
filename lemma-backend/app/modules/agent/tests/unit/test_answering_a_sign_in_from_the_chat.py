"""Answering `browser_sign_in` through the ordinary approval decision.

The card in the conversation resolves a waiting sign-in the same way it
resolves an `ask_user`, because that is the path the transcript subscribes to.
Answering anywhere else -- as the standalone page's own endpoint did -- resolved
the pause server-side while the screen the person was looking at never heard
about it, so the card kept asking over a run that had already carried on.

What moves with the answer is the capture. The standalone page reads the
browser at the moment the button is pressed, because it is the only surface
that can say "the browser holds nothing for this site" while the person is
still in front of it; it passes `saved` along and this has nothing left to do.
The card has no browser of its own, so the capture happens here.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4, uuid7

import pytest

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)

pytestmark = pytest.mark.unit

_ORIGIN = "https://asur.work"


class _Uow:
    def after_commit(self, _callback) -> None:
        return None


class _SignInService:
    """Stands in for the real service, recording how it was called."""

    def __init__(self, result: tuple[bool, str | None] | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []
        self.closed = 0

    async def capture(self, **kwargs) -> tuple[bool, str | None]:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def close(self) -> None:
        self.closed += 1


def _install(monkeypatch: pytest.MonkeyPatch, service: _SignInService) -> None:
    # Patched at the source modules because `_capture_for_resume` imports both
    # inside the function -- a module-local alias would not be the name it
    # binds.
    monkeypatch.setattr(
        "app.core.api.dependencies.get_uow_factory",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.modules.web_login.contracts.SignInService",
        lambda _factory: service,
    )


async def _answer(
    *,
    decision: AgentRunApprovalDecision,
    response: dict[str, object],
) -> dict[str, object]:
    builder = ResumeToolReturnBuilder(_Uow(), None)
    return await builder._browser_sign_in_return(
        tool_args={"origin": _ORIGIN},
        decision=decision,
        response=response,
        user_id=uuid4(),
        conversation_id=uuid7(),
    )


def test_the_stand_in_is_called_the_way_the_real_service_can_be() -> None:
    """The double takes `**kwargs`, so on its own it would happily certify a
    call the real `capture` cannot accept. This binds the same arguments
    against the real signature, so a rename there fails here rather than at
    the one moment somebody is waiting to be let in."""
    from inspect import signature

    from app.modules.web_login.contracts import SignInService

    signature(SignInService.capture).bind(
        SignInService,
        user_id=uuid4(),
        origin=_ORIGIN,
        conversation_id=uuid7(),
        force=True,
    )


@pytest.mark.asyncio
async def test_the_chat_answer_captures_the_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The card carries no `saved`, so this is the surface that must read the
    browser. Without it the run resumed with the login discarded and the person
    was asked for the same site again on the next run."""
    service = _SignInService((True, None))
    _install(monkeypatch, service)

    content = await _answer(decision=AgentRunApprovalDecision.APPROVE_ONCE, response={})

    assert content["outcome"] == "signed_in"
    assert content["saved"] is True
    assert service.calls[0]["origin"] == _ORIGIN
    # Nobody is in front of a page that could offer "save anyway", so the
    # capture is not allowed to stop and ask.
    assert service.calls[0]["force"] is True
    assert service.closed == 1


@pytest.mark.asyncio
async def test_a_page_that_already_captured_is_not_read_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`saved` in the payload is the standalone page's own answer. Capturing
    again would read a browser the person has already walked away from."""
    service = _SignInService((True, None))
    _install(monkeypatch, service)

    content = await _answer(
        decision=AgentRunApprovalDecision.APPROVE_ONCE,
        response={"saved": False, "saved_detail": "nothing to keep"},
    )

    assert service.calls == []
    assert content["saved"] is False
    assert "nothing to keep" in str(content["message"])


@pytest.mark.asyncio
async def test_an_unkept_login_still_resumes_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A browser that cannot be read answers `(False, why)` rather than
    raising, and the person has signed in either way. The run carries on and
    the agent is told what it costs."""
    service = _SignInService((False, "the relay is unreachable"))
    _install(monkeypatch, service)

    content = await _answer(decision=AgentRunApprovalDecision.APPROVE_ONCE, response={})

    assert content["success"] is True
    assert content["outcome"] == "signed_in"
    assert content["saved"] is False
    assert "the relay is unreachable" in str(content["message"])


@pytest.mark.asyncio
async def test_a_broken_capture_does_not_strand_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deadlock this exists to prevent.

    By the time this runs the caller has already committed the execution claim,
    so raising writes no tool return at all -- and a paused call with no return
    is a conversation nobody can leave: the composer is locked on the pause,
    the only thing that supersedes a stale pause runs when a message is sent,
    and sending a message is what the lock prevents.

    So the run continues and the agent is told. `success` is still true because
    the *sign-in* succeeded; only keeping it for next time did not.
    """
    service = _SignInService(RuntimeError("the store is down"))
    _install(monkeypatch, service)

    content = await _answer(decision=AgentRunApprovalDecision.APPROVE_ONCE, response={})

    assert content["success"] is True
    assert content["outcome"] == "signed_in"
    assert content["saved"] is False
    assert "could not be read back" in str(content["message"])
    assert service.closed == 1


@pytest.mark.asyncio
async def test_declining_never_touches_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declining is an answer, not a failure: the run continues, and the agent
    is told not to ask for this site again."""
    service = _SignInService((True, None))
    _install(monkeypatch, service)

    content = await _answer(decision=AgentRunApprovalDecision.DENY, response={})

    assert service.calls == []
    assert content["outcome"] == "declined"
    assert content["success"] is True
