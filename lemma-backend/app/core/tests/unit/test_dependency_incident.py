from __future__ import annotations

from app.core.observability import dependency_incident
from app.core.observability.dependency_incident import DependencyIncident
from app.core.infrastructure.db import session as session_module


class _Logger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def warning(self, event: str, **fields) -> None:
        self.records.append(("warning", event, fields))

    def info(self, event: str, **fields) -> None:
        self.records.append(("info", event, fields))


def test_incident_emits_one_pair_after_sustained_failures(monkeypatch) -> None:
    clock = iter((0.0, 1.0, 2.0, 3.0, 10.0))
    monkeypatch.setattr(dependency_incident.time, "monotonic", lambda: next(clock))
    logger = _Logger()
    incident = DependencyIncident("unit.redis", logger=logger)  # type: ignore[arg-type]

    for _ in range(4):
        incident.record_failure(error_type="TimeoutError")
    incident.record_success()

    assert [record[:2] for record in logger.records] == [
        ("warning", "dependency.degraded"),
        ("info", "dependency.recovered"),
    ]
    assert logger.records[0][2]["failure_count"] == 3
    assert logger.records[1][2]["failure_count"] == 4
    assert logger.records[1][2]["incident_duration_ms"] == 10000.0


def test_short_transient_failure_never_emits(monkeypatch) -> None:
    clock = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(dependency_incident.time, "monotonic", lambda: next(clock))
    logger = _Logger()
    incident = DependencyIncident("unit.redis", logger=logger)  # type: ignore[arg-type]
    incident.record_failure(error_type="TimeoutError")
    incident.record_failure(error_type="TimeoutError")
    incident.record_success()
    assert logger.records == []


def test_db_pool_pressure_emits_one_transition_pair(monkeypatch) -> None:
    clock = iter((0.0, 1.0, 2.0, 3.0, 10.0))
    monkeypatch.setattr(dependency_incident.time, "monotonic", lambda: next(clock))
    logger = _Logger()
    monkeypatch.setattr(
        session_module,
        "_pool_pressure_incident",
        DependencyIncident(
            "database_pool_capacity",
            logger=logger,  # type: ignore[arg-type]
            degradation_threshold=3,
        ),
    )

    class _Pool:
        """Pool of 5. There is no overflow, so size() is the whole ceiling."""

        def __init__(self, checked_out: int) -> None:
            self._checked_out = checked_out

        def size(self) -> int:
            return 5

        def checkedout(self) -> int:
            return self._checked_out

    class _ConnectionRecord:
        def __init__(self, checked_out: int) -> None:
            self.pool = _Pool(checked_out)

    for _ in range(4):
        session_module._log_pool_utilization(None, _ConnectionRecord(4))
    session_module._log_pool_utilization(None, _ConnectionRecord(1))

    assert [record[:2] for record in logger.records] == [
        ("warning", "dependency.degraded"),
        ("info", "dependency.recovered"),
    ]
    assert logger.records[0][2]["dependency"] == "database_pool_capacity"
