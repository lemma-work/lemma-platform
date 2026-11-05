"""A committed change to a row an agent's brief lists replaces its pod's token.

A real SQLAlchemy session over SQLite, so the flush and commit hooks run as
they do against Postgres; only the Redis write is recorded instead of sent.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Uuid, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.infrastructure.db import pod_inventory

pytestmark = pytest.mark.unit


class _Base(DeclarativeBase):
    pass


class _Listed(_Base):
    __tablename__ = "listed"
    __pod_inventory__ = "pod_id"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    pod_id: Mapped[UUID] = mapped_column(Uuid)


class _Unlisted(_Base):
    __tablename__ = "unlisted"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    pod_id: Mapped[UUID] = mapped_column(Uuid)


@pytest.fixture
def bumped(monkeypatch) -> list[list[UUID]]:
    pod_inventory.install()
    seen: list[list[UUID]] = []
    monkeypatch.setattr(pod_inventory, "_schedule_bump", seen.append)
    return seen


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_a_committed_write_bumps_its_pod_once(bumped, session) -> None:
    pod = uuid4()
    session.add(_Listed(pod_id=pod))
    session.flush()
    session.add(_Listed(pod_id=pod))
    session.commit()

    assert bumped == [[pod]]


def test_a_rolled_back_write_bumps_nothing(bumped, session) -> None:
    session.add(_Listed(pod_id=uuid4()))
    session.flush()
    session.rollback()
    session.commit()

    assert bumped == []


def test_a_row_the_brief_does_not_list_bumps_nothing(bumped, session) -> None:
    session.add(_Unlisted(pod_id=uuid4()))
    session.commit()

    assert bumped == []


def test_a_change_and_a_delete_bump_their_pod(bumped, session) -> None:
    pod = uuid4()
    row = _Listed(pod_id=pod)
    session.add(row)
    session.commit()
    row.pod_id = pod
    session.delete(row)
    session.commit()

    assert bumped == [[pod], [pod]]
