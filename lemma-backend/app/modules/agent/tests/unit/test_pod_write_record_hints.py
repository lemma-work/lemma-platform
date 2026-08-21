"""The empty-`data` rejection has to tell the model what to send instead.

A dogfood run watched an agent make three identical `pod_write_record` calls with
`data: {}`, each correctly rejected and each rejected the same way. "`data` must
be a non-empty object" says the payload is wrong without saying what would be
right, so the retry is a guess.

The schema is deliberately NOT tightened to fix this: `data` is
`JsonObject | str | None` because OpenAI requires `properties` on every object
schema, and a free-form dynamic-column object serializes as `properties: {}`,
which models read as "no fields" and fill with `{}`. Removing the union would
reinstate the silent blank-row write the guard exists to stop.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.tools.pod.models import PodWriteRecordRequest
from app.modules.agent.tools.pod.pod_data_access import (
    empty_data_error,
    writable_column_names,
)


def _services(columns: list[str]):
    table = SimpleNamespace(columns=[SimpleNamespace(name=name) for name in columns])

    class _Tables:
        async def get_table(self, pod_id, table_name, ctx):
            return table

    return SimpleNamespace(table=_Tables(), ctx=SimpleNamespace(pod_id=uuid4()))


@pytest.mark.asyncio
async def test_writable_columns_exclude_platform_managed_ones():
    """Naming `id`/`created_at`/`user_id` in a write hint would invite the model
    to set columns the platform owns."""
    services = _services(
        ["id", "created_at", "updated_at", "user_id", "title", "status"]
    )
    assert await writable_column_names(services, "tickets") == ["title", "status"]


@pytest.mark.asyncio
async def test_empty_data_error_names_the_table_s_columns():
    services = _services(["id", "title", "status"])
    request = PodWriteRecordRequest(action="create", table_name="tickets", data={})

    message = await empty_data_error(services, request)

    assert "must be a non-empty object" in message
    # The part that makes the retry land.
    assert 'Columns on "tickets": title, status.' in message


@pytest.mark.asyncio
async def test_empty_data_error_survives_a_table_with_no_writable_columns():
    """No columns to name is not a reason to produce a broken sentence."""
    services = _services(["id", "created_at"])
    request = PodWriteRecordRequest(action="update", table_name="blank", data={})

    message = await empty_data_error(services, request)

    assert "nothing was written." in message
    assert "Columns on" not in message


def test_data_keeps_its_string_escape_hatch():
    """Guards the deliberate union: a JSON-encoded string must still be accepted
    and decoded, because that is the unambiguous path for models that can't
    express a free-form object."""
    request = PodWriteRecordRequest(
        action="create", table_name="tickets", data='{"title": "Q3"}'
    )
    assert request.data == {"title": "Q3"}
