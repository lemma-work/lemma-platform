"""A workload denial has to name the resource, not just the permission.

`MISSING_WORKLOAD_RESOURCE_GRANT: Missing permission datastore.table.read` is
unactionable for a function that touches two tables — it names the verb and never
the noun. Four skill files told operators the error names the resource, and their
recovery instructions depended on information the error did not carry.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization.context import (
    AuthorizationDecision,
    ResourceRef,
    ResourceType,
    _denial_message,
    _denial_resource_details,
)
from app.core.domain.errors import DomainError


def _denial(resource, name, reason="MISSING_WORKLOAD_RESOURCE_GRANT"):
    return AuthorizationDecision(
        False, reason, "datastore.table.read", resource, resource_name=name
    )


def test_message_names_the_resource_and_the_fix():
    decision = _denial(
        ResourceRef.table(uuid4(), uuid4()),
        "customers",
    )
    message = _denial_message("datastore.table.read", decision)
    assert "datastore_table 'customers'" in message
    # The fix is one command, and the denial is when you need it.
    assert "permissions add" in message
    assert "customers:read,write" in message


def test_details_carry_the_resource_for_callers_that_parse():
    decision = _denial(ResourceRef.table(uuid4(), uuid4()), "customers")
    assert _denial_resource_details(decision) == {
        "resource_type": "datastore_table",
        "resource_name": "customers",
    }


def test_folder_denials_use_the_path():
    resource = ResourceRef(
        resource_type=ResourceType.FOLDER, pod_id=uuid4(), path="/knowledge"
    )
    decision = AuthorizationDecision(
        False,
        "MISSING_WORKLOAD_RESOURCE_GRANT",
        "folder.read",
        resource,
        resource_name="/knowledge",
    )
    message = _denial_message("folder.read", decision)
    assert "folder '/knowledge'" in message
    assert "/knowledge:read" in message


def test_unresolvable_name_still_names_the_type():
    """A deleted resource can't be named — say the type rather than nothing."""
    decision = _denial(ResourceRef.table(uuid4(), uuid4()), None)
    message = _denial_message("datastore.table.read", decision)
    assert "on datastore_table" in message
    # No half-built fix instruction when there's no name to put in it.
    assert "permissions add" not in message


def test_no_resource_keeps_the_old_message():
    decision = AuthorizationDecision(False, "INSUFFICIENT_PERMISSION", "pod.read", None)
    assert _denial_message("pod.read", decision) == "Missing permission pod.read"


def test_non_grant_denials_name_the_resource_but_offer_no_grant_fix():
    """A PERSONAL resource is denied by privacy; no grant unlocks it, so pointing
    at `permissions add` would be actively wrong."""
    decision = _denial(
        ResourceRef.table(uuid4(), uuid4()), "notes", reason="PERSONAL_RESOURCE_DENIED"
    )
    message = _denial_message("datastore.table.read", decision)
    assert "datastore_table 'notes'" in message
    assert "permissions add" not in message


@pytest.mark.asyncio
async def test_require_raises_with_the_enriched_message():
    from app.core.authorization.context import ActorType, Context

    resource = ResourceRef.table(uuid4(), uuid4())

    class _Authorizer:
        async def authorize(self, ctx, permission_id, resource=None):
            return _denial(resource, "customers")

        async def accessible_resource_ids(self, *args, **kwargs):
            return frozenset()

    ctx = Context(
        actor_type=ActorType.AGENT,
        actor_id=uuid4(),
        authorizer=_Authorizer(),
    )
    with pytest.raises(DomainError) as excinfo:
        await ctx.require("datastore.table.read", resource)
    assert "customers" in str(excinfo.value)
    assert excinfo.value.details["resource_name"] == "customers"
