"""The one statement must return exactly what the Python walk returned.

``get_visible_file_ids`` used to load every file row in a pod, hydrate all of
them, collect their ancestor paths, re-query by those paths and re-derive
inheritance in Python. It now asks the database once. That is a rewrite of an
*authorization* answer, so speed is not the property under test here — set
equality is.

Both implementations are run against the same real pod, over a tree built to
exercise every branch the CASE has: POD, PERSONAL owned and unowned, RESTRICTED
with and without a grant, a grant on an ancestor folder, and a readable file
underneath an unreadable folder. The old implementation is still present
(``get_visible_file_ids_for_items`` serves the short-list callers), so it can be
run side by side rather than reconstructed from memory.

Divergence is expected in exactly one case and asserted separately below: a
*gap* in the folder chain. The Python walk stopped climbing at the first
missing ancestor row and let everything above it go unchecked; the statement
does not stop.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e_authz import create_role_visibility_context
from app.modules.test_support.query_counting import counted_queries

pytestmark = pytest.mark.e2e


def _authorizer(session):
    """A real repository over the test session, and the authorizer above it."""
    repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(session))
    return (
        FileAuthorizer(DatastoreAuthorization(object()), repository, PathResolver()),
        repository,
    )


async def _legacy_visible_file_ids(authorizer, repository, *, pod_id, ctx, user_id):
    """What ``get_visible_file_ids`` did before: every row, then walk."""
    items = await repository.get_all_by_datastore(pod_id)
    return await authorizer.get_visible_file_ids_for_items(
        pod_id=pod_id,
        requester_user_id=user_id,
        items=items,
        ctx=ctx,
    )


@pytest.fixture
async def visibility_pod(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
) -> dict:
    """A pod exercising every branch of the visibility CASE."""
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="visibility-equiv",
        custom_role="EQUIV_OPERATORS",
    )
    pod_id = ctx["pod_id"]
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]

    granted = f"/granted-{suffix}"
    ungranted = f"/ungranted-{suffix}"
    shared = f"/shared-{suffix}"

    await owner.create_folder(granted, visibility="RESTRICTED")
    await owner.create_folder(f"{granted}/deep", visibility="RESTRICTED")
    leaf = await owner.upload_file(
        "leaf.md", b"granted body", directory_path=f"{granted}/deep",
        visibility="RESTRICTED", search_enabled=False,
    )
    await owner.create_folder(ungranted, visibility="RESTRICTED")
    hidden = await owner.upload_file(
        "hidden.md", b"hidden body", directory_path=ungranted,
        visibility="RESTRICTED", search_enabled=False,
    )
    # A POD-visible file under a RESTRICTED folder: readable on its own row,
    # and the ancestor walk is the only thing that hides it.
    pod_under_restricted = await owner.upload_file(
        "open.md", b"open body", directory_path=ungranted, search_enabled=False,
    )
    await owner.create_folder(shared)
    plain = await owner.upload_file(
        "plain.md", b"plain body", directory_path=shared, search_enabled=False,
    )
    # The owner's own personal file: PERSONAL, owned by someone else entirely
    # from the operator's point of view.
    personal = await owner.upload_file(
        "private.md", b"private body", directory_path="/me", search_enabled=False,
    )

    grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "folder",
                    "resource_name": granted,
                    "permission_ids": ["folder.read"],
                }
            ]
        },
    )
    assert grant.status_code == status.HTTP_200_OK, grant.text

    return {
        **ctx,
        "granted_root": granted,
        "ungranted_root": ungranted,
        "leaf": leaf,
        "hidden": hidden,
        "pod_under_restricted": pod_under_restricted,
        "plain": plain,
        "personal": personal,
    }


@pytest.mark.parametrize("principal", ["custom_viewer", "viewer", "editor"])
async def test_the_statement_matches_the_walk_for_every_principal(
    db_session, visibility_pod, principal
) -> None:
    pod_id = UUID(visibility_pod["pod_id"])
    user_id = UUID(visibility_pod[principal]["id"])

    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    authorizer, repository = _authorizer(db_session)

    legacy = await _legacy_visible_file_ids(
        authorizer, repository, pod_id=pod_id, ctx=ctx, user_id=user_id
    )
    batched = await authorizer.get_visible_file_ids(
        pod_id=pod_id, requester_user_id=user_id, ctx=ctx
    )

    paths = {
        item.id: item.path for item in await repository.get_all_by_datastore(pod_id)
    }
    assert batched == legacy, (
        "the visibility statement disagreed with the walk it replaces.\n"
        f"  only in the statement: {sorted(paths.get(i, i) for i in batched - legacy)}\n"
        f"  only in the walk:      {sorted(paths.get(i, i) for i in legacy - batched)}"
    )
    assert legacy, f"{principal} sees nothing at all -- the fixture proves nothing"


async def test_the_granted_folder_is_visible_and_the_ungranted_one_is_not(
    db_session, visibility_pod
) -> None:
    """Pin the answer itself, not only that two implementations agree.

    Equivalence alone would be satisfied by both being wrong in the same way,
    which is exactly what a shared helper makes possible.
    """
    pod_id = UUID(visibility_pod["pod_id"])
    user_id = UUID(visibility_pod["custom_viewer"]["id"])
    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    authorizer, _ = _authorizer(db_session)

    visible = await authorizer.get_visible_file_ids(
        pod_id=pod_id, requester_user_id=user_id, ctx=ctx
    )

    assert UUID(visibility_pod["leaf"]["id"]) in visible, (
        "a folder grant did not cascade to a RESTRICTED file two levels down"
    )
    assert UUID(visibility_pod["hidden"]["id"]) not in visible, (
        "a RESTRICTED file under an ungranted folder was visible"
    )
    assert UUID(visibility_pod["personal"]["id"]) not in visible, (
        "another user's PERSONAL file was visible"
    )
    assert UUID(visibility_pod["plain"]["id"]) in visible, (
        "an ordinary POD file was not visible to a pod member"
    )
    assert UUID(visibility_pod["pod_under_restricted"]["id"]) not in visible, (
        "a POD file under a RESTRICTED folder the caller cannot read was "
        "visible -- the ancestor walk is what this test exists to protect"
    )


@pytest.mark.parametrize("principal", ["custom_viewer", "viewer"])
async def test_the_split_agrees_with_the_filter_it_is_projected_from(
    db_session, visibility_pod, principal
) -> None:
    """Search sends the smaller side; both sides must describe the same pod.

    ``file_visibility_split`` projects the identical predicate as a boolean
    instead of applying it as a filter, which is exactly the kind of near-copy
    that drifts. It also has a failure mode the WHERE-clause form cannot have:
    the value is read back into Python, so its declared SQL type matters.
    """
    pod_id = UUID(visibility_pod["pod_id"])
    user_id = UUID(visibility_pod[principal]["id"])
    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    authorizer, repository = _authorizer(db_session)

    filtered = await authorizer.get_visible_file_ids(
        pod_id=pod_id, requester_user_id=user_id, ctx=ctx
    )
    visible, hidden = await repository.file_visibility_split(
        pod_id=pod_id, ctx=ctx, walk_ancestors=True
    )
    every_id = {item.id for item in await repository.get_all_by_datastore(pod_id)}

    assert visible == filtered, "the projected predicate disagreed with the filter"
    assert visible | hidden == every_id, "the split lost rows"
    assert not (visible & hidden), "a file was both visible and hidden"

    pushed = await authorizer.visibility_filter(pod_id=pod_id, ctx=ctx)
    assert {i for i in every_id if pushed.allows(i)} == filtered, (
        "the filter search actually pushes down admits a different set than the "
        f"visibility answer it was built from (direction={pushed.direction.value})"
    )
    assert len(pushed.file_ids) <= max(len(visible), len(hidden)), (
        "the pushed side is not the smaller one"
    )


async def test_visibility_costs_one_statement_regardless_of_pod_size(
    db_session, visibility_pod
) -> None:
    """The count is the fix; the equality above is what makes it safe."""
    pod_id = UUID(visibility_pod["pod_id"])
    user_id = UUID(visibility_pod["custom_viewer"]["id"])
    service = AuthorizationDataService(db_session)
    ctx = await service.build_user_context(user_id=user_id, pod_id=pod_id)
    authorizer, repository = _authorizer(db_session)

    with counted_queries() as legacy_statements:
        await _legacy_visible_file_ids(
            authorizer, repository, pod_id=pod_id, ctx=ctx, user_id=user_id
        )
    with counted_queries() as batched_statements:
        await authorizer.get_visible_file_ids(
            pod_id=pod_id, requester_user_id=user_id, ctx=ctx
        )

    assert len(batched_statements) == 1, (
        f"visibility took {len(batched_statements)} statements, not one:\n"
        + "\n".join(f"  - {s.strip()[:160]}" for s in batched_statements)
    )
    assert len(legacy_statements) > len(batched_statements), (
        "the walk did not cost more than the statement, so this fixture is not "
        "measuring what the change was made for"
    )
