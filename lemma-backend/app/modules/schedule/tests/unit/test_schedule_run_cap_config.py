"""``max_concurrent_runs`` in a schedule's config: kept, typed, and bounded."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
    ScheduleUpdateEntity,
    normalize_datastore_schedule_config,
    schedule_run_cap,
)


def test_datastore_normalization_keeps_the_cap() -> None:
    """Normalization rebuilds the config; the cap must survive it."""
    config = normalize_datastore_schedule_config(
        {"table_name": "orders", "operations": ["insert"], "max_concurrent_runs": 3}
    )

    assert config["max_concurrent_runs"] == 3
    assert schedule_run_cap(config) == 3


@pytest.mark.parametrize("schedule_type", list(ScheduleType))
def test_every_schedule_type_reads_the_cap_off_its_config(schedule_type) -> None:
    config = {
        ScheduleType.TIME: {"cron": "0 * * * *"},
        ScheduleType.WEBHOOK: {"source": "github"},
        ScheduleType.DATASTORE: {"table_name": "orders", "operations": ["INSERT"]},
    }[schedule_type] | {"max_concurrent_runs": 2}
    schedule = ScheduleEntity(
        user_id=uuid4(), pod_id=uuid4(), schedule_type=schedule_type, config=config
    )

    typed = schedule.time_config or schedule.webhook_config or schedule.datastore_config
    assert typed is not None
    assert typed.max_concurrent_runs == 2


def test_no_cap_is_none() -> None:
    assert schedule_run_cap({"cron": "0 * * * *"}) is None
    assert schedule_run_cap(None) is None


@pytest.mark.parametrize("cap", [0, -1, 1001, "2", 2.5, True])
def test_an_invalid_cap_is_refused_wherever_a_config_is_written(cap) -> None:
    config = {"cron": "0 * * * *", "max_concurrent_runs": cap}

    with pytest.raises(ValueError, match="max_concurrent_runs"):
        schedule_run_cap(config)
    with pytest.raises(ValidationError, match="max_concurrent_runs"):
        ScheduleCreateEntity(
            user_id=uuid4(),
            pod_id=uuid4(),
            schedule_type=ScheduleType.TIME,
            config=config,
        )
    with pytest.raises(ValidationError, match="max_concurrent_runs"):
        ScheduleUpdateEntity(config=config)
