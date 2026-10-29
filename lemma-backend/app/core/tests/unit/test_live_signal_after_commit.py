"""A signal queued in a transaction is published when that transaction commits,
however many the session has committed before it."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.live import signals

pytestmark = pytest.mark.unit


class _Sync:
    """What ``signal_after_commit`` reads off an ``AsyncSession``."""

    def __init__(self, sync) -> None:
        self.sync_session = sync


@pytest.fixture
def published(monkeypatch) -> list[tuple[UUID, list[signals.Signal]]]:
    seen: list[tuple[UUID, list[signals.Signal]]] = []
    monkeypatch.setattr(
        signals, "publish_signals", lambda pod_id, sent: seen.append((pod_id, sent))
    )
    return seen


def test_every_commit_publishes_what_it_queued(published) -> None:
    engine = create_engine("sqlite://")
    pod = uuid4()
    with Session(engine) as session:
        for turn in range(3):
            session.connection()
            signals.signal_after_commit(_Sync(session), pod, {"turn": turn})  # type: ignore[arg-type]
            session.commit()
    engine.dispose()

    assert published == [
        (pod, [{"turn": 0}]),
        (pod, [{"turn": 1}]),
        (pod, [{"turn": 2}]),
    ]


def test_a_rolled_back_signal_is_never_published(published) -> None:
    engine = create_engine("sqlite://")
    pod = uuid4()
    with Session(engine) as session:
        session.connection()
        signals.signal_after_commit(_Sync(session), pod, {"turn": "lost"})  # type: ignore[arg-type]
        session.rollback()
        session.connection()
        signals.signal_after_commit(_Sync(session), pod, {"turn": "kept"})  # type: ignore[arg-type]
        session.commit()
    engine.dispose()

    assert published == [(pod, [{"turn": "kept"}])]
