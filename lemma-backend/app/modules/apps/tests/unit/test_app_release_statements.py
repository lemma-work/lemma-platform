"""The compiled shape of the release upsert.

Asserted here rather than against a database because what makes the upsert
correct is exactly which columns it does and does not overwrite on conflict, and
that is visible in the SQL. A revived release must keep its number and its place
in the history; only the tombstone and the paths may move.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.apps.domain.entities import AppReleaseEntity
from app.modules.apps.infrastructure.repositories import _record_release_statement

# Compiling an ORM insert configures every mapper in the registry, and the app
# models pull in relationships that name classes from other modules.
from app.modules.identity.infrastructure import models as _identity_models  # noqa: F401
from app.modules.pod.infrastructure import models as _pod_models  # noqa: F401

pytestmark = pytest.mark.unit


def _sql() -> str:
    app_id = uuid4()
    statement = _record_release_statement(
        AppReleaseEntity(
            app_id=app_id,
            version="a" * 64,
            dist_root_path=f"releases/{'a' * 64}/dist/",
            dist_archive_path=f"releases/{'a' * 64}/dist/archive.zip",
            source_archive_path="source/bb/archive.zip",
            source_digest="bb",
            created_by=uuid4(),
        )
    )
    return str(statement.compile(dialect=postgresql.dialect())).lower()


def _set_clause() -> str:
    """Just the DO UPDATE SET list -- RETURNING names every column, so a naive
    split would make "this column is not updated" assertions vacuously false."""
    return _sql().split("do update set", 1)[1].split("returning", 1)[0]


def test_the_upsert_targets_the_version_constraint():
    sql = _sql()
    assert "on conflict on constraint uq_app_release_version" in sql
    assert "do update" in sql


def test_a_revived_release_has_its_tombstone_cleared():
    # The whole point: the bytes are back, so `pruned_at` has stopped being true.
    set_clause = _set_clause()
    assert "pruned_at" in set_clause


def test_a_revived_release_keeps_its_number_and_its_place_in_history():
    """Updating these would reorder the history rather than restore a build."""
    set_clause = _set_clause()
    assert "release_number" not in set_clause
    assert "created_at" not in set_clause
    assert "created_by" not in set_clause


def test_the_release_number_is_allocated_inside_the_insert():
    # Not read first: a read-then-write lets two racing uploads pick the same
    # number, which is what the deleted `next_release_number` did.
    sql = _sql()
    assert "max(app_releases.release_number)" in sql


def test_a_dist_only_upload_does_not_clobber_a_known_source_with_null():
    # Retention deletes source blobs nothing references, so losing the pointer
    # here would lose the source.
    set_clause = _set_clause()
    assert "coalesce" in set_clause
