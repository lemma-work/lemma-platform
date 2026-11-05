"""The sandbox integration tests truncate tables, so where they connect matters.

The default database URL is the dev stack's. Taken whole, these tests would
truncate a developer's sandboxes; shared across xdist workers, they truncated
each other's rows mid-test.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.modules.workspace.tests.integration.conftest import (
    ISOLATED_DATABASE_PREFIX,
    isolated_database_url,
)

pytestmark = pytest.mark.unit

DEV_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/lemma"


def test_the_configured_database_is_never_the_one_used() -> None:
    url = make_url(isolated_database_url(DEV_URL, "gw0", 4242))

    assert url.database is not None
    assert url.database != "lemma"
    assert url.database.startswith(ISOLATED_DATABASE_PREFIX)
    assert (url.host, url.port, url.username) == ("localhost", 5432, "postgres")


def test_each_worker_and_each_run_gets_its_own_database() -> None:
    names = {
        make_url(isolated_database_url(DEV_URL, worker, pid)).database
        for worker in ("gw0", "gw1", "master")
        for pid in (1, 2)
    }

    assert len(names) == 6


def test_the_fixture_database_is_not_the_configured_one(
    sandbox_database_url: str,
) -> None:
    database = make_url(sandbox_database_url).database

    assert database is not None
    assert database.startswith(ISOLATED_DATABASE_PREFIX)
