"""What the routing statements actually ask for.

Asserted on the compiled SQL because these predicates are load-bearing in a way
the result set hides: each one *removes* a surface that would otherwise be
routed to, so dropping one produces more candidates rather than an error, and
the wrong pod answers a message instead of nothing happening.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.repositories.surface_routing_sql import (
    routing_surfaces,
)

pytestmark = pytest.mark.unit


def _sql(**narrowing) -> str:
    return str(
        routing_surfaces(SurfacePlatform.SLACK.value, **narrowing).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_the_base_statement_is_the_platform_in_a_live_pod():
    statement = _sql()
    assert "surface_type" in statement
    assert "status" in statement
    assert "pods" in statement and "is_deleted" in statement, (
        "the live-pod join is what stops a deleted pod's surface answering"
    )
    assert "ORDER BY agent_surfaces.created_at, agent_surfaces.id" in statement, (
        "the documented tiebreak, which also picks the identity surface"
    )


def test_shared_credentials_excludes_both_a_custom_mode_and_a_bound_account():
    """Either half alone lets the wrong surface through.

    A bound bot on SYSTEM credentials is still somebody's own bot, and a
    CUSTOM-mode surface with no account is still not the shared one. The
    original Python required both, and so does this.
    """
    statement = _sql(system_credentials_only=True)
    assert "agent_surfaces.account_id IS NULL" in statement
    assert "agent_surfaces.credential_mode = 'SYSTEM'" in statement


def test_narrowings_compose_rather_than_replace():
    """A configuration flow passes a receiver list and a workspace together."""
    statement = _sql(surface_ids=[uuid4()], external_workspace_id="T123")
    assert "agent_surfaces.id IN" in statement
    assert "agent_surfaces.external_workspace_id = 'T123'" in statement


def test_an_absent_receiver_list_is_not_an_empty_one():
    """Absent selects the platform; empty selects nothing. Collapsing them routes
    a native receiver's event to every surface on the platform."""
    assert "agent_surfaces.id IN" not in _sql()
    assert "agent_surfaces.id IN" in _sql(surface_ids=[])


def test_a_blank_workspace_is_not_a_predicate():
    """`tenant_id` is optional at every caller, and an empty one means "unknown",
    not "the surface whose workspace is the empty string".

    Matched on the comparison rather than the column name, which is in the
    projection of every one of these statements.
    """
    assert "external_workspace_id = " not in _sql(external_workspace_id="")
    assert "external_workspace_id = " not in _sql(external_workspace_id=None)
    assert "external_workspace_id = " in _sql(external_workspace_id="T123")
