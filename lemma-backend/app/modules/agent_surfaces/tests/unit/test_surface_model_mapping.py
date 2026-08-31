"""A row naming something the enum no longer has must not 500 the page.

`agent_surfaces.surface_type` and `.event_mode` are plain string columns, and
both enums have lost members: `GMAIL`/`OUTLOOK` when email became Resend and
only Resend, `COMPOSIO_TRIGGER` when polled triggers went. No migration deletes
those rows -- deliberately, since they are configuration somebody chose -- and
`SurfacePlatform` is a `StrEnum`, so `SurfacePlatform("GMAIL")` raises a bare
`ValueError`. Not a `DomainError`, so it reaches the catch-all handler as a 500
with no actionable message.

`list_by_pod` maps a whole page, so one such row took the pod's entire surface
list with it, every agent-to-human notification for the pod
(`notification_channels`), and the user's cross-pod list (`user_surfaces_service`
loops every pod they belong to). `get_account_conflict_in_org` is org-wide and
platform-blind, so it did the same to the *creation* of an unrelated surface.

Nothing in the module called `to_entity()` from a test before this file, which
is why a rename of the enum could pass every suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    SurfaceEventMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface

pytestmark = pytest.mark.unit


def _row(**overrides) -> AgentSurface:
    """A detached model, enough for the mapping to run. No session involved."""
    now = datetime.now(UTC)
    row = AgentSurface()
    row.id = overrides.pop("id", uuid4())
    row.created_at = now
    row.updated_at = now
    row.pod_id = uuid4()
    row.name = "slack"
    row.agent_id = None
    row.surface_type = "SLACK"
    row.mode = "DM"
    row.event_mode = "WEBHOOK"
    row.credential_mode = "SYSTEM"
    row.config = {}
    row.account_id = None
    row.external_workspace_id = None
    row.external_tenant_id = None
    row.external_channel_id = None
    row.surface_identity_id = None
    row.surface_identity_username = None
    row.status = "ACTIVE"
    row.surface_identity_email = None
    row.webhook_secret = None
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_a_live_row_still_maps_with_every_field_intact() -> None:
    row = _row(
        surface_type="RESEND",
        name="email",
        surface_identity_email="agent@ops.example",
    )
    entity = row.to_entity_or_none()
    assert entity is not None
    assert entity.surface_type is SurfacePlatform.RESEND
    assert entity.event_mode is SurfaceEventMode.WEBHOOK
    assert entity.surface_identity_email == "agent@ops.example"
    assert entity.id == row.id


@pytest.mark.parametrize("retired", ["GMAIL", "OUTLOOK"])
def test_a_retired_platform_reads_as_absent_rather_than_raising(retired) -> None:
    assert _row(surface_type=retired).to_entity_or_none() is None


def test_a_retired_event_mode_reads_as_absent_too() -> None:
    """The same hazard, one column over, and equally undeleted by any migration."""
    assert _row(event_mode="COMPOSIO_TRIGGER").to_entity_or_none() is None


def test_the_legacy_dotted_form_still_maps() -> None:
    """Rows written before the column stored a bare member name."""
    entity = _row(surface_type="SurfacePlatform.SLACK").to_entity_or_none()
    assert entity is not None
    assert entity.surface_type is SurfacePlatform.SLACK


def test_a_null_surface_type_still_defaults_rather_than_dropping() -> None:
    entity = _row(surface_type=None).to_entity_or_none()
    assert entity is not None
    assert entity.surface_type is SurfacePlatform.SLACK


def test_to_entity_itself_still_raises_on_a_retired_value() -> None:
    """Code holding a validated entity may assume the mapping worked.

    Only the read paths tolerate; the mapping stays strict, so a genuine bug
    that writes an impossible value is not swallowed.
    """
    with pytest.raises(ValueError):
        _row(surface_type="GMAIL").to_entity()


def test_the_skip_is_logged_where_an_operator_will_see_it() -> None:
    """Silently dropping a surface somebody configured is its own failure."""
    from app.core.log.event_catalog import EVENT_CATALOG

    spec = EVENT_CATALOG["agent_surfaces.surface_row.retired_value_skipped.degraded"]
    assert spec.level == "warning"
    assert {"surface_id", "column", "value"} <= spec.fields
