"""The oldest-transaction gauge reports the main database and every registered one."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core.infrastructure.work import gauges


class _Session:
    def __init__(self, main_age: float | None) -> None:
        self.main_age = main_age

    async def execute(self, statement, params=None):
        return SimpleNamespace(all=list)

    async def scalar(self, statement):
        assert statement is gauges.OLDEST_TRANSACTION_AGE
        return self.main_age


def _opener(main_age: float | None):
    @asynccontextmanager
    async def open_session():
        yield _Session(main_age)

    return open_session


def _ages() -> dict[str, float]:
    return {
        obs.attributes["database"]: obs.value
        for obs in gauges._observe_transaction_age(None)  # type: ignore[arg-type]
    }


@pytest.fixture(autouse=True)
def _clean():
    gauges._snapshot.forget()
    yield
    gauges._transaction_age_probes.clear()
    gauges._snapshot.forget()


async def test_main_and_each_registered_database_are_reported() -> None:
    async def datastore() -> float:
        return 42.5

    gauges.register_transaction_age_probe("datastore", datastore)
    await gauges.sample(_opener(7.0))
    assert _ages() == {"main": 7.0, "datastore": 42.5}


async def test_no_open_transaction_reads_zero_not_absent() -> None:
    await gauges.sample(_opener(None))
    assert _ages() == {"main": 0.0}
