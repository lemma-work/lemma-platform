from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.modules.agent.tools.messaging.models import MessagePersonRequest
from app.modules.agent.tools.messaging.pydantic_adapter import (
    _attribution,
    _resolve_pod_member,
    message_person,
)

POD = uuid4()
OWNER = uuid4()
COLLEAGUE = uuid4()
CONVERSATION = uuid4()

MEMBERS = [
    SimpleNamespace(user_id=OWNER, user_email="dj@acme.com", user_name="Deepak"),
    SimpleNamespace(user_id=COLLEAGUE, user_email="priya@acme.com", user_name="Priya"),
]


class _Outcome:
    def __init__(self):
        self.notification_id = uuid4()
        self.conversation_id = CONVERSATION
        self.delivered_via = SimpleNamespace(value="TELEGRAM")
        self.reached_a_chat_surface = True


def _ctx(user_id=OWNER):
    return SimpleNamespace(
        deps=SimpleNamespace(
            pod_id=POD,
            user_id=user_id,
            conversation_id=CONVERSATION,
            agent_name="ops",
            agent_display_name="Ops Assistant",
            agent_run_id=uuid4(),
        )
    )


async def _call(person, *, origin, captured, caller_user_id=OWNER):
    async def fake_notify(**kwargs):
        captured.update(kwargs)
        return _Outcome()

    async def fake_origin(_conversation_id):
        return origin

    with (
        patch(
            "app.modules.agent.tools.messaging.pydantic_adapter._pod_members",
            return_value=MEMBERS,
        ),
        patch(
            "app.modules.agent.tools.messaging.pydantic_adapter._conversation_origin",
            new=fake_origin,
        ),
        patch(
            "app.modules.agent_surfaces.services.surface_display_delivery.notify_member",
            new=fake_notify,
        ),
    ):
        return await message_person(
            _ctx(caller_user_id), MessagePersonRequest(person=person, message="hi")
        )


class TestResolvingWho:
    def test_email_is_exact(self):
        assert _resolve_pod_member(MEMBERS, "priya@acme.com") == (COLLEAGUE, None)

    def test_a_name_works_when_it_is_unambiguous(self):
        assert _resolve_pod_member(MEMBERS, "Priya") == (COLLEAGUE, None)

    def test_an_ambiguous_name_is_an_error_not_a_guess(self):
        """Messaging the wrong colleague is not a mistake the agent can undo."""
        twins = [
            SimpleNamespace(user_id=uuid4(), user_email="a@x.com", user_name="Sam"),
            SimpleNamespace(user_id=uuid4(), user_email="b@x.com", user_name="Sam"),
        ]
        user_id, error = _resolve_pod_member(twins, "Sam")
        assert user_id is None and "More than one" in error

    def test_a_stranger_is_refused(self):
        user_id, error = _resolve_pod_member(MEMBERS, "nobody@example.com")
        assert user_id is None and "No pod member" in error

    def test_attribution_names_the_agent_and_the_human_behind_it(self):
        assert _attribution(MEMBERS, OWNER, "Ops Assistant") == (
            "_Ops Assistant, working for Deepak_"
        )

    def test_attribution_degrades_to_the_agent_alone(self):
        assert _attribution(MEMBERS, uuid4(), "Ops Assistant") == "_Ops Assistant_"


class TestReachingYourOwnRunOwner:
    """The headline use case: a schedule telling you something."""

    @pytest.mark.asyncio
    async def test_a_scheduled_run_may_tell_its_owner(self):
        captured: dict = {}
        result = await _call("dj@acme.com", origin="SCHEDULE_RUN", captured=captured)
        assert result.success, result.error
        assert captured["recipient_user_id"] == OWNER

    @pytest.mark.asyncio
    async def test_it_links_back_to_the_run_that_produced_it(self):
        """Otherwise the notification is a bare message with no trail back to
        the work."""
        captured: dict = {}
        await _call("dj@acme.com", origin="SCHEDULE_RUN", captured=captured)
        assert captured["conversation_id"] == CONVERSATION

    @pytest.mark.asyncio
    async def test_a_workflow_run_may_too(self):
        captured: dict = {}
        result = await _call("dj@acme.com", origin="WORKFLOW_RUN", captured=captured)
        assert result.success

    @pytest.mark.asyncio
    async def test_but_a_person_started_conversation_is_told_to_just_reply(self):
        """The signal is provenance, not attention — see the note on the guard."""
        captured: dict = {}
        result = await _call("dj@acme.com", origin=None, captured=captured)
        assert not result.success
        assert "answer here instead" in result.error
        assert captured == {}, "nothing should have been sent"


class TestReachingSomeoneElse:
    @pytest.mark.asyncio
    async def test_a_colleague_gets_no_conversation_hint(self):
        """An agent running as one person must not be able to drop a colleague
        into its own thread."""
        captured: dict = {}
        result = await _call("priya@acme.com", origin=None, captured=captured)
        assert result.success
        assert captured["recipient_user_id"] == COLLEAGUE
        assert captured["conversation_id"] is None

    @pytest.mark.asyncio
    async def test_the_message_is_attributed(self):
        captured: dict = {}
        await _call("priya@acme.com", origin=None, captured=captured)
        assert captured["attribution"] == "_Ops Assistant, working for Deepak_"

    @pytest.mark.asyncio
    async def test_the_agent_is_told_where_it_landed(self):
        result = await _call("priya@acme.com", origin=None, captured={})
        assert result.delivered_via == "TELEGRAM"
        assert "priya@acme.com" in result.message
