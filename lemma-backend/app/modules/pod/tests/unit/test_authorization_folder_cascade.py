"""Unit tests for folder-grant path-prefix cascade (Workstream A).

The full DB-backed cascade behaviour is validated in the datastore e2e suite;
these cover the pure helpers and the SQL-shape guard that keeps non-folder
resource types on exact-id matching.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.authorization.context import PrincipalRef, ResourceRef, ResourceType
from app.core.authorization.authorizer import Authorizer
from app.core.authorization.sql_actions import _grant_exists_for_action
from app.modules.datastore.infrastructure.models.datastore_models import DatastoreFile


def test_ancestor_folder_paths() -> None:
    assert Authorizer._ancestor_folder_paths("/a/b/c.md") == ["/", "/a", "/a/b"]
    assert Authorizer._ancestor_folder_paths("/x") == ["/"]
    assert Authorizer._ancestor_folder_paths("/") == ["/"]


@pytest.mark.asyncio
async def test_acceptable_ids_none_for_non_folder_types() -> None:
    authz = Authorizer(session=None)  # session unused for non-folder types
    for resource_type in (
        ResourceType.AGENT,
        ResourceType.FUNCTION,
        ResourceType.WORKFLOW,
        ResourceType.DATASTORE_TABLE,
        ResourceType.SCHEDULE,
    ):
        ref = ResourceRef(
            resource_type=resource_type, resource_id=uuid4(), pod_id=uuid4()
        )
        assert await authz._acceptable_grant_resource_ids(ref) is None


@pytest.mark.asyncio
async def test_acceptable_ids_for_folder_includes_self_pod_and_ancestors() -> None:
    ancestor_id = uuid4()
    self_id = uuid4()
    pod_id = uuid4()

    class _Result:
        def scalars(self):
            class _S:
                def all(self_inner):
                    return [ancestor_id]

            return _S()

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

    authz = Authorizer(session=_Session())
    ref = ResourceRef(
        resource_type=ResourceType.DOCUMENT,
        resource_id=self_id,
        pod_id=pod_id,
        path="/voice/clip.md",
    )
    acceptable = set(await authz._acceptable_grant_resource_ids(ref))
    # self id, the pod-wide namespace sentinel, and the ancestor folder id
    assert acceptable == {self_id, pod_id, ancestor_id}


def _compile(expr) -> str:
    return str(expr.compile(dialect=postgresql.dialect()))


def test_grant_sql_exact_match_for_non_folder_types() -> None:
    group = frozenset({PrincipalRef(type="ROLE", id=uuid4())})
    sql = _compile(
        _grant_exists_for_action(
            resource_type=ResourceType.AGENT,
            resource_id_col=DatastoreFile.id,  # arbitrary column; only shape matters
            pod_id_col=DatastoreFile.pod_id,
            principal_group=group,
            action="agent.read",
            resource_path_col=DatastoreFile.path,
        )
    )
    # Non-folder type must not introduce the aliased prefix-join or LIKE/left.
    assert "datastore_files_1" not in sql
    assert "left(" not in sql.lower()
    assert "resource_id =" in sql


def test_grant_sql_cascades_for_document_with_path() -> None:
    group = frozenset({PrincipalRef(type="ROLE", id=uuid4())})
    sql = _compile(
        _grant_exists_for_action(
            resource_type=ResourceType.DOCUMENT,
            resource_id_col=DatastoreFile.id,
            pod_id_col=DatastoreFile.pod_id,
            principal_group=group,
            action="folder.read",
            resource_path_col=DatastoreFile.path,
        )
    )
    # Cascade branch joins to an aliased datastore_files and does a prefix check.
    assert "datastore_files AS datastore_files_1" in sql
    assert "left(" in sql.lower()


def test_grant_sql_document_without_path_stays_exact() -> None:
    group = frozenset({PrincipalRef(type="ROLE", id=uuid4())})
    sql = _compile(
        _grant_exists_for_action(
            resource_type=ResourceType.DOCUMENT,
            resource_id_col=DatastoreFile.id,
            pod_id_col=DatastoreFile.pod_id,
            principal_group=group,
            action="folder.read",
            resource_path_col=None,
        )
    )
    assert "left(" not in sql.lower()
    assert "resource_id =" in sql
