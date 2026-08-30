"""Which surfaces share an inbound address — the only ones a user must choose between."""

from __future__ import annotations

from uuid import uuid4

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.surface_address import (
    contended_surface_ids,
    inbound_address_key,
)


def _surface(platform=SurfacePlatform.TELEGRAM, **overrides) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        **overrides,
    )


def test_shared_system_bot_is_one_address_across_pods():
    first = _surface()
    second = _surface()

    assert inbound_address_key(first) == inbound_address_key(second)
    assert contended_surface_ids([first, second]) == {first.id, second.id}


def test_own_bot_has_its_own_address():
    """A Telegram token answers for exactly one surface, so it can never contend."""
    own = _surface(account_id=uuid4(), credential_mode=SurfaceCredentialMode.CUSTOM)
    shared = _surface()

    assert inbound_address_key(own) != inbound_address_key(shared)
    assert contended_surface_ids([own, shared]) == set()


def test_same_handle_on_two_surfaces_still_contends():
    """Whatever produced it, one @handle answering twice is a real choice."""
    handle = "@lemma_bot"
    first = _surface(
        credential_mode=SurfaceCredentialMode.CUSTOM, surface_identity_username=handle
    )
    second = _surface(
        credential_mode=SurfaceCredentialMode.CUSTOM, surface_identity_username=handle
    )

    assert contended_surface_ids([first, second]) == {first.id, second.id}


def test_one_slack_app_is_one_address_per_workspace():
    """Inbound is matched against the workspace, so only same-workspace surfaces
    can take each other's messages."""
    here = _surface(SurfacePlatform.SLACK, external_workspace_id="T_ACME")
    also_here = _surface(SurfacePlatform.SLACK, external_workspace_id="T_ACME")
    elsewhere = _surface(SurfacePlatform.SLACK, external_workspace_id="T_OTHER")

    assert contended_surface_ids([here, also_here, elsewhere]) == {
        here.id,
        also_here.id,
    }


def test_resend_mailboxes_share_a_key_but_not_an_address():
    """One API key, a catch-all domain, a unique address per surface."""
    first = _surface(
        SurfacePlatform.RESEND, surface_identity_email="one@pods.lemma.run"
    )
    second = _surface(
        SurfacePlatform.RESEND, surface_identity_email="two@pods.lemma.run"
    )

    assert contended_surface_ids([first, second]) == set()


def test_unidentified_surfaces_fall_back_to_their_own_id():
    """Nothing known yet is not evidence of a shared address."""
    first = _surface(
        SurfacePlatform.RESEND, credential_mode=SurfaceCredentialMode.CUSTOM
    )
    second = _surface(
        SurfacePlatform.RESEND, credential_mode=SurfaceCredentialMode.CUSTOM
    )

    assert contended_surface_ids([first, second]) == set()
