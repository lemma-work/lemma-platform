"""What a person sees when the app cannot be pinned to a pod they can edit.

Two ways an in-platform event fails to name one surface: the person may edit
nothing here, or they may edit several pods and not have said which. Neither is
an error -- both have a screen -- and this is about that screen actually
rendering.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.channel_setup import (
    ChannelSetupKind,
    ChannelSetupRequest,
)

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.services.app_event_handler import AppEventHandler
from app.modules.agent_surfaces.services.configuration_prompt import (
    prompt_for_configuration,
)

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

    await prompt_for_configuration(
        handler.uow.session,
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

    await prompt_for_configuration(
        handler.uow.session,
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

    await prompt_for_configuration(
        handler.uow.session,
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


async def test_the_channel_setup_modal_opens_with_the_connection_handed_back(
    monkeypatch,
):
    """`views.open` runs inside the webhook request, so nothing may hold a
    pooled connection across it -- including a session that resolving the
    clicker has written to, which `connection_released` alone will not let go.
    """
    events: list[str] = []

    class _Uow:
        async def commit(self) -> None:
            events.append("commit")

    uow = _Uow()
    uow.session = SimpleNamespace(info={"lemma_uow": uow}, new=[object()])
    adapter = AsyncMock()
    adapter.channel_name = AsyncMock(return_value="#general")

    async def _open_modal(**_: object) -> None:
        events.append("views.open")

    adapter.open_channel_setup_modal = _open_modal
    monkeypatch.setattr(
        "app.modules.agent.contracts.channels.surface_agent_identity",
        AsyncMock(return_value=None),
    )

    await _handler(uow=uow)._open_channel_setup(
        adapter,
        {"access_token": "xoxb"},
        ChannelSetupRequest(
            kind=ChannelSetupKind.OPEN,
            tenant_id="T1",
            actor_external_user_id="U-1",
            trigger_id="T-1",
            channel_id="C-1",
        ),
        SimpleNamespace(id=uuid4(), agent_id=uuid4()),
    )

    assert events == ["commit", "views.open"]


async def test_a_channel_setting_that_could_not_be_saved_is_reported_in_the_channel(
    monkeypatch, caplog
):
    """The modal closes before the write; a failed write left the person
    believing the channel was configured, with only a debug line behind it."""
    rolled_back: list[bool] = []

    class _Uow:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            rolled_back.append(True)

    uow = _Uow()
    uow.session = SimpleNamespace(info={"lemma_uow": uow}, new=[])
    surface = SimpleNamespace(
        id=uuid4(),
        agent_id=uuid4(),
        pod_id=uuid4(),
        config=SimpleNamespace(channels=[]),
    )
    adapter = AsyncMock()
    adapter.parse_channel_setup = AsyncMock(
        return_value=ChannelSetupRequest(
            kind=ChannelSetupKind.SUBMIT,
            tenant_id="T1",
            channel_id="C-1",
            actor_external_user_id="U-1",
        )
    )
    surfaces = AsyncMock()
    surfaces.update = AsyncMock(side_effect=OperationalError("UPDATE", {}, None))
    access = SimpleNamespace(
        _authorized_configuration_surfaces=AsyncMock(
            return_value=([surface], uuid4(), [(surface, None)])
        ),
        _pick_configuration_surface=AsyncMock(return_value=(surface, None)),
    )
    monkeypatch.setattr(
        "app.modules.agent.contracts.channels.surface_agent_identity",
        AsyncMock(return_value=None),
    )
    handler = _handler(
        uow=uow,
        surface_repository=surfaces,
        adapter_registry=SimpleNamespace(get=lambda platform: adapter),
        access=access,
    )

    with caplog.at_level(logging.WARNING):
        handled = await handler.try_handle_channel_setup(
            SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
        )

    assert handled is True
    assert rolled_back == [True]
    adapter.send_channel_setup_prompt.assert_awaited_once()
    sent = adapter.send_channel_setup_prompt.await_args.kwargs
    assert sent["channel_id"] == "C-1"
    assert sent["user_id"] == "U-1"
    assert "could not save" in sent["configuration_error"]
    assert "channel_setup_failed" in caplog.text
