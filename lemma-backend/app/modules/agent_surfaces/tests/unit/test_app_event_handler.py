"""What a person sees when the app cannot be pinned to a pod they can edit.

Two ways an in-platform event fails to name one surface: the person may edit
nothing here, or they may edit several pods and not have said which. Neither is
an error -- both have a screen -- and this is about that screen actually
rendering.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.app_event_handler import AppEventHandler

pytestmark = pytest.mark.asyncio


def _handler(**overrides) -> AppEventHandler:
    defaults = {
        "uow": SimpleNamespace(session=None),
        "surface_repository": AsyncMock(),
        "adapter_registry": SimpleNamespace(get=lambda platform: None),
        "credential_resolver": SimpleNamespace(
            for_surface=AsyncMock(return_value={"access_token": "xoxb"})
        ),
        "pod_membership_port": AsyncMock(),
        "external_user_repository": AsyncMock(),
        "access": SimpleNamespace(_surface_choice_labels=AsyncMock(return_value=[])),
    }
    return AppEventHandler(**{**defaults, **overrides})


def _home_opened() -> ParsedSurfaceLifecycleEvent:
    return ParsedSurfaceLifecycleEvent(
        platform=SurfacePlatform.SLACK,
        kind=SurfaceLifecycleKind.HOME_OPENED,
        actor_external_user_id="U-VISITOR",
    )


async def test_a_visitor_with_no_pod_is_told_so_rather_than_crashing():
    """The one screen this path exists to render.

    It did not render. `publish_home_view` takes a required `agent_name` on
    every implementation and this call omitted it, so opening the Slack App Home
    as somebody with no access to a connected pod raised `TypeError` — and the
    message explaining what to do next reached nobody.

    Nothing caught it because `SurfacePlatformAdapterPort` did not declare the
    operation, so the type checker never compared the call to a signature, and
    no test drove this branch. The name is the product's own default responder:
    no surface has been chosen here, so there is no agent whose name it could be.
    """
    adapter = AsyncMock()
    handler = _handler()

    await handler._prompt_for_configuration(
        adapter=adapter,
        parsed=_home_opened(),
        actor_external_user_id="U-VISITOR",
        credentials={"access_token": "xoxb"},
        surface_choices=None,
    )

    sent = adapter.publish_home_view.await_args.kwargs
    assert sent["agent_name"] == DEFAULT_RESPONDER_NAME
    assert sent["user_id"] == "U-VISITOR"
    assert "access to a connected Lemma pod" in sent["access_message"]


async def test_a_member_of_several_pods_is_offered_the_choice_not_an_error():
    """The other branch: authorized somewhere, so the message is a chooser."""
    adapter = AsyncMock()
    handler = _handler()

    await handler._prompt_for_configuration(
        adapter=adapter,
        parsed=_home_opened(),
        actor_external_user_id="U-VISITOR",
        credentials={"access_token": "xoxb"},
        surface_choices=[("surface-a", "Ops"), ("surface-b", "Support")],
    )

    sent = adapter.publish_home_view.await_args.kwargs
    assert sent["surface_choices"] == [("surface-a", "Ops"), ("surface-b", "Support")]
    assert sent["access_message"] is None
    assert sent["agent_name"] == DEFAULT_RESPONDER_NAME


async def test_a_freshly_joined_channel_is_offered_to_the_person_who_added_it():
    adapter = AsyncMock()
    handler = _handler()
    parsed = ParsedSurfaceLifecycleEvent(
        platform=SurfacePlatform.SLACK,
        kind=SurfaceLifecycleKind.JOINED_CHANNEL,
        actor_external_user_id="U-ADDER",
        external_channel_id="C999",
    )

    await handler._prompt_for_configuration(
        adapter=adapter,
        parsed=parsed,
        actor_external_user_id="U-ADDER",
        credentials={"access_token": "xoxb"},
        surface_choices=None,
    )

    sent = adapter.send_channel_setup_prompt.await_args.kwargs
    assert sent["channel_id"] == "C999"
    assert sent["user_id"] == "U-ADDER"
    assert "Only a Lemma pod editor" in sent["configuration_error"]


async def test_an_event_with_no_actor_is_not_answered_at_all():
    """There is nobody to answer, and `publish_home_view` would be a `None` id."""
    adapter = AsyncMock()
    surface = SimpleNamespace(id=uuid4(), pod_id=uuid4())
    handler = _handler()

    await handler._answer_unroutable_lifecycle(
        adapter=adapter,
        parsed=ParsedSurfaceLifecycleEvent(
            platform=SurfacePlatform.SLACK,
            kind=SurfaceLifecycleKind.HOME_OPENED,
            actor_external_user_id=None,
        ),
        candidates=[surface],
        authorized=[],
    )

    adapter.publish_home_view.assert_not_awaited()
