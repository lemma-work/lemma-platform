"""A role scoped to one pod must not govern another.

`assign_roles` resolves roles by (organization, pod) before writing, so it does
not produce a cross-scope assignment today. That is a property of one caller,
not of the data: `role_assignments` carries no constraint tying a principal's
scope to its role's. The per-pod query used to re-check it in its WHERE clause;
a listing that spans pods fetches with ANY_POD and cannot, so it re-checks each
row instead, and this is what says so.

It matters most for the organization member. That one principal's rows are
merged into *every* pod a listing builds, so a pod-scoped role reaching it
would be granted in all of them rather than in the one it names.
"""

from __future__ import annotations

from uuid import uuid4

from app.core.authorization.role_queries import roles_applying_to_pod

ORG_MEMBER = uuid4()
POD_A = uuid4()
POD_B = uuid4()


def _row(role_pod_id):
    """One join row: principal, role, name, permission, the role's own pod."""
    return (ORG_MEMBER, uuid4(), "Reviewer", "agent.read", role_pod_id)


def test_an_organization_role_governs_every_pod():
    organization_wide = _row(None)

    assert roles_applying_to_pod([organization_wide], POD_A) == [organization_wide]
    assert roles_applying_to_pod([organization_wide], POD_B) == [organization_wide]


def test_a_pod_role_governs_its_own_pod():
    scoped_to_a = _row(POD_A)

    assert roles_applying_to_pod([scoped_to_a], POD_A) == [scoped_to_a]


def test_a_pod_role_does_not_follow_its_principal_into_another_pod():
    """The one that would be a privilege escalation rather than a lost row."""
    scoped_to_a = _row(POD_A)

    assert roles_applying_to_pod([scoped_to_a], POD_B) == []


def test_only_the_foreign_rows_are_dropped():
    """A mixed set keeps what governs the pod and loses only what does not."""
    organization_wide = _row(None)
    scoped_to_a = _row(POD_A)
    scoped_to_b = _row(POD_B)

    kept = roles_applying_to_pod([organization_wide, scoped_to_a, scoped_to_b], POD_A)

    assert kept == [organization_wide, scoped_to_a]
