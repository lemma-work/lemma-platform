"""Write-time normalization of DATASTORE schedule operations.

Regression tests for the P0 where CLI-provided aliases (operations:
["create"]) were stored verbatim while the matcher compared canonical values
("INSERT"), so schedules silently never fired.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
    normalize_datastore_schedule_config,
)
from app.modules.schedule.api.schemas.schedule_schemas import CreateScheduleRequest
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import ScheduleUpdateEntity
from app.modules.schedule.services.schedule_service import ScheduleService


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["insert"], ["INSERT"]),
        (["INSERT"], ["INSERT"]),
        (["update", "delete"], ["UPDATE", "DELETE"]),
        (["insert", "insert", "update"], ["INSERT", "UPDATE"]),  # dedupe
        (["INSERT", "UPDATE", "DELETE"], ["INSERT", "UPDATE", "DELETE"]),
    ],
)
def test_normalize_datastore_schedule_config_matrix(raw, expected):
    config = normalize_datastore_schedule_config(
        {"table_name": "users", "operations": raw}
    )
    assert config["operations"] == expected
    assert config["table_name"] == "users"


@pytest.mark.parametrize("raw_config", [{}, {"operations": None}, {"operations": []}])
def test_normalize_rejects_missing_or_empty_operations(raw_config):
    with pytest.raises(ValueError, match="declare operations explicitly"):
        normalize_datastore_schedule_config({"table_name": "users", **raw_config})


@pytest.mark.parametrize("op", [["upsert"], ["create"], ["write"], ["read"]])
def test_normalize_rejects_unknown_operations(op):
    # Aliases (create, write) are no longer accepted — only INSERT/UPDATE/DELETE.
    with pytest.raises(ValueError):
        normalize_datastore_schedule_config(
            {"table_name": "users", "operations": op}
        )


def test_normalize_stores_conditions_in_their_expanded_form():
    """The saved blob reads the same however the author wrote it."""
    config = normalize_datastore_schedule_config(
        {
            "table_name": "tickets",
            "operations": ["UPDATE"],
            "when": {"status": "approved", "owner": {"changed": True}},
        }
    )
    assert config["when"] == {
        "status": {"equals": "approved"},
        "owner": {"changed": True},
    }


def test_normalize_keeps_operator_aliases_readable():
    config = normalize_datastore_schedule_config(
        {
            "table_name": "tickets",
            "operations": ["UPDATE"],
            "when": {"status": {"from": "pending", "in": ["a", "b"]}},
        }
    )
    assert config["when"]["status"] == {"from": "pending", "in": ["a", "b"]}


def test_normalize_rejects_a_condition_no_declared_operation_can_satisfy():
    """A trigger that can never fire should fail loudly at save time."""
    with pytest.raises(ValueError, match="only an UPDATE can satisfy"):
        normalize_datastore_schedule_config(
            {
                "table_name": "tickets",
                "operations": ["INSERT"],
                "when": {"status": {"changed": True}},
            }
        )
    with pytest.raises(ValueError, match="only an INSERT or UPDATE can satisfy"):
        normalize_datastore_schedule_config(
            {
                "table_name": "tickets",
                "operations": ["DELETE"],
                "when": {"status": {"to": "approved"}},
            }
        )


def test_normalize_rejects_a_misspelled_operator():
    with pytest.raises(ValueError):
        normalize_datastore_schedule_config(
            {
                "table_name": "tickets",
                "operations": ["UPDATE"],
                "when": {"status": {"equal": "approved"}},
            }
        )


def test_conditions_are_optional():
    config = normalize_datastore_schedule_config(
        {"table_name": "tickets", "operations": ["INSERT"]}
    )
    assert "when" not in config


def test_create_schedule_request_validates_conditions():
    with pytest.raises(ValidationError, match="only an UPDATE can satisfy"):
        CreateScheduleRequest(
            schedule_type=ScheduleType.DATASTORE,
            workflow_name="my-flow",
            config={
                "table_name": "tickets",
                "operations": ["INSERT"],
                "when": {"status": {"from": "pending"}},
            },
        )


def test_schedule_create_entity_canonicalizes_case():
    entity = ScheduleCreateEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "users", "operations": ["insert"]},
    )
    assert entity.config["operations"] == ["INSERT"]


def test_schedule_create_entity_rejects_missing_operations():
    with pytest.raises(ValidationError, match="declare operations explicitly"):
        ScheduleCreateEntity(
            user_id=uuid4(),
            pod_id=uuid4(),
            schedule_type=ScheduleType.DATASTORE,
            config={"table_name": "users"},
        )


def test_schedule_create_entity_leaves_other_types_alone():
    entity = ScheduleCreateEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        config={"cron": "0 9 * * *"},
    )
    assert entity.config == {"cron": "0 9 * * *"}


def test_create_schedule_request_rejects_missing_operations():
    with pytest.raises(ValidationError, match="declare operations explicitly"):
        CreateScheduleRequest(
            schedule_type=ScheduleType.DATASTORE,
            workflow_name="my-flow",
            config={"table_name": "users"},
        )


def test_create_schedule_request_canonicalizes_case():
    request = CreateScheduleRequest(
        schedule_type=ScheduleType.DATASTORE,
        workflow_name="my-flow",
        config={"table_name": "users", "operations": ["insert"]},
    )
    assert request.config["operations"] == ["INSERT"]


@pytest.mark.asyncio
async def test_update_path_normalizes_and_requires_operations():
    uow = AsyncMock()
    repo = AsyncMock()
    service = ScheduleService(
        uow=uow,
        schedule_repository=repo,
        external_schedule_writer=AsyncMock(),
    )
    existing = ScheduleEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "users", "operations": ["INSERT"]},
    )

    update_data = await service._resolve_update_target(
        existing,
        ScheduleUpdateEntity(
            config={"table_name": "users", "operations": ["update", "delete"]}
        ),
    )
    assert update_data["config"]["operations"] == ["UPDATE", "DELETE"]

    with pytest.raises(ScheduleValidationError, match="declare operations explicitly"):
        await service._resolve_update_target(
            existing,
            ScheduleUpdateEntity(config={"table_name": "users"}),
        )
