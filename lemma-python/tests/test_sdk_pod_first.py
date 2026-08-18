from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from lemma_sdk import Lemma, Pod
from lemma_sdk.errors import LemmaAPIError, LemmaConfigError
from lemma_sdk.openapi_client.models.datastore_count_response import (
    DatastoreCountResponse,
)
from lemma_sdk.openapi_client.models.function_run_response import FunctionRunResponse
from lemma_sdk.openapi_client.models.operation_execution_response import (
    OperationExecutionResponse,
)
from lemma_sdk.openapi_client.models.record_create_response_record_create import (
    RecordCreateResponseRecordCreate,
)
from lemma_sdk.openapi_client.models.schedule_run_response import ScheduleRunResponse
from lemma_sdk.transport import LemmaTransport


class StubTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.generated = object()

    def call(self, endpoint, *path_args, body=None, body_model=None, **kwargs):
        self.calls.append(
            {
                "endpoint": endpoint.__name__,
                "path_args": path_args,
                "body": body,
                "body_model": getattr(body_model, "__name__", None),
                "kwargs": kwargs,
            }
        )
        if endpoint.__name__.endswith("record_create"):
            return RecordCreateResponseRecordCreate.from_dict(
                {"id": "rec-1", **body["data"]}
            )
        if ".record_bulk_" in endpoint.__name__:
            # Every bulk endpoint answers with an affected-row count. Without
            # this branch the stub returns None and the facades all report 0,
            # which would let a broken delegation pass.
            counted = body.get("records") or body.get("record_ids") or []
            return DatastoreCountResponse.from_dict({"count": len(counted)})
        if endpoint.__name__.endswith("function_run"):
            return FunctionRunResponse.from_dict(
                {
                    "completed_at": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "function_id": "33333333-3333-4333-8333-333333333333",
                    "id": "44444444-4444-4444-8444-444444444444",
                    "started_at": "2026-01-01T00:00:00Z",
                    "status": "COMPLETED",
                    "user_id": "55555555-5555-4555-8555-555555555555",
                    "input_data": body.get("input_data"),
                    "output_data": {"ok": True},
                }
            )
        if endpoint.__name__.endswith("schedule_run_retry"):
            return ScheduleRunResponse.from_dict(
                {
                    "attempts": 0,
                    "created_at": "2026-01-01T00:00:00Z",
                    "id": "66666666-6666-4666-8666-666666666666",
                    "llm_output": {},
                    "metadata": {},
                    "payload": {},
                    "redrive_of_run_id": "77777777-7777-4777-8777-777777777777",
                    "redriven_by_user_id": "55555555-5555-4555-8555-555555555555",
                    "schedule_id": "33333333-3333-4333-8333-333333333333",
                    "source_event_id": "manual-retry:77777777-7777-4777-8777-777777777777",
                    "status": "RECEIVED",
                    "target_kind": "WORKFLOW",
                    "target_run_id": "88888888-8888-4888-8888-888888888888",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "user_id": "55555555-5555-4555-8555-555555555555",
                }
            )
        if endpoint.__name__.endswith("connector_operation_execute"):
            return OperationExecutionResponse.from_dict(
                {
                    "result": {"sent": True},
                }
            )
        return None

    def close(self) -> None:
        pass


def test_pod_table_create_binds_pod_and_returns_typed_record():
    transport = StubTransport()
    lemma = Lemma(token="token", base_url="https://api.example.test", org_id="11111111-1111-4111-8111-111111111111")
    lemma._transport = transport
    pod = lemma.pod("22222222-2222-4222-8222-222222222222")

    record = pod.table("tickets").create({"title": "Refund request"})

    # Records now return the bare record object (no {data} envelope).
    assert isinstance(record, dict)
    assert record["id"] == "rec-1"
    assert record["title"] == "Refund request"
    assert transport.calls[0] == {
        "endpoint": "lemma_sdk.openapi_client.api.records.record_create",
        "path_args": (UUID("22222222-2222-4222-8222-222222222222"), "tickets"),
        "body": {"data": {"title": "Refund request"}},
        "body_model": "CreateRecordRequest",
        "kwargs": {},
    }


def _bound_pod(transport: StubTransport) -> Pod:
    lemma = Lemma(token="token", base_url="https://api.example.test", org_id="11111111-1111-4111-8111-111111111111")
    lemma._transport = transport
    return lemma.pod("22222222-2222-4222-8222-222222222222")


def test_pod_table_bulk_create_writes_every_row_in_one_call():
    """The reason this method exists.

    Without it the only batch path was ``pod.records.bulk_create``, so a caller
    holding a table handle wrote N rows as N ``create`` calls -- N round trips
    from wherever the code runs back to the API. A benchmark probe did exactly
    that and spent 17 of its 17.3 seconds on them.
    """
    transport = StubTransport()
    pod = _bound_pod(transport)
    rows = [{"title": f"row-{index}"} for index in range(50)]

    count = pod.table("tickets").bulk_create(rows)

    assert count == 50
    assert len(transport.calls) == 1, "50 rows must cost one round trip, not 50"
    assert transport.calls[0] == {
        "endpoint": "lemma_sdk.openapi_client.api.records.record_bulk_create",
        "path_args": (UUID("22222222-2222-4222-8222-222222222222"), "tickets"),
        "body": {"records": rows, "upsert": False},
        "body_model": "BulkCreateRecordsRequest",
        "kwargs": {},
    }


def test_pod_table_bulk_create_forwards_upsert():
    transport = StubTransport()
    pod = _bound_pod(transport)

    pod.table("tickets").bulk_create([{"id": "rec-1", "title": "again"}], upsert=True)

    assert transport.calls[0]["body"]["upsert"] is True


def test_pod_table_bulk_update_binds_the_table_it_was_opened_on():
    transport = StubTransport()
    pod = _bound_pod(transport)

    count = pod.table("tickets").bulk_update(
        [{"id": "rec-1", "status": "resolved"}, {"id": "rec-2", "status": "open"}]
    )

    assert count == 2
    assert (
        transport.calls[0]["endpoint"]
        == "lemma_sdk.openapi_client.api.records.record_bulk_update"
    )
    assert transport.calls[0]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
        "tickets",
    )


def test_pod_table_bulk_delete_binds_the_table_it_was_opened_on():
    transport = StubTransport()
    pod = _bound_pod(transport)

    count = pod.table("tickets").bulk_delete(["rec-1", "rec-2", "rec-3"])

    assert count == 3
    assert (
        transport.calls[0]["endpoint"]
        == "lemma_sdk.openapi_client.api.records.record_bulk_delete"
    )
    assert transport.calls[0]["body"] == {"record_ids": ["rec-1", "rec-2", "rec-3"]}


def test_pod_records_list_serializes_structured_filter_and_sort_clauses():
    transport = StubTransport()
    pod = Pod(
        "22222222-2222-4222-8222-222222222222",
        org_id="11111111-1111-4111-8111-111111111111",
        token="token",
        base_url="https://api.example.test",
    )
    pod._transport = transport
    pod.records._transport = transport

    pod.records.list(
        "issues",
        limit=1,
        filter=[{"field": "status", "op": "eq", "value": "open"}],
        sort=[{"field": "updated_at", "direction": "desc"}],
    )

    call = transport.calls[0]
    assert call["endpoint"] == "lemma_sdk.openapi_client.api.records.record_list"
    assert call["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
        "issues",
    )
    assert call["kwargs"]["limit"] == 1
    assert call["kwargs"]["offset"] == 0
    assert call["kwargs"]["filter_"] == ['{"field":"status","op":"eq","value":"open"}']
    assert call["kwargs"]["sort"] == ['{"field":"updated_at","direction":"desc"}']


def test_pod_functions_run_binds_pod_and_returns_typed_run():
    transport = StubTransport()
    pod = Pod(
        "22222222-2222-4222-8222-222222222222",
        org_id="11111111-1111-4111-8111-111111111111",
        token="token",
        base_url="https://api.example.test",
    )
    pod._transport = transport
    pod.functions._transport = transport

    run = pod.functions.run("triage_ticket", {"ticket_id": "rec-1"})

    assert isinstance(run, FunctionRunResponse)
    assert str(run.id) == "44444444-4444-4444-8444-444444444444"
    assert transport.calls[0]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
        "triage_ticket",
    )
    assert transport.calls[0]["body"] == {"input_data": {"ticket_id": "rec-1"}}


def test_pod_schedules_retry_run_binds_schedule_and_source_run():
    transport = StubTransport()
    pod = Pod(
        "22222222-2222-4222-8222-222222222222",
        org_id="11111111-1111-4111-8111-111111111111",
        token="token",
        base_url="https://api.example.test",
    )
    pod._transport = transport
    pod.schedules._transport = transport

    run = pod.schedules.retry_run(
        "33333333-3333-4333-8333-333333333333",
        "77777777-7777-4777-8777-777777777777",
    )

    assert isinstance(run, ScheduleRunResponse)
    assert str(run.redrive_of_run_id) == "77777777-7777-4777-8777-777777777777"
    assert transport.calls[0]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
        UUID("33333333-3333-4333-8333-333333333333"),
        UUID("77777777-7777-4777-8777-777777777777"),
    )


def test_pod_connectors_execute_uses_bound_org_id():
    transport = StubTransport()
    pod = Pod(
        "22222222-2222-4222-8222-222222222222",
        org_id="11111111-1111-4111-8111-111111111111",
        token="token",
        base_url="https://api.example.test",
    )
    pod._transport = transport
    pod.connectors._transport = transport
    pod.connectors.operations._parent._transport = transport

    result = pod.connectors.execute(
        "gmail",
        "GMAIL_SEND_EMAIL",
        {"to": "a@example.com", "subject": "Hi"},
    )

    assert isinstance(result, OperationExecutionResponse)
    assert result.result == {"sent": True}
    assert transport.calls[0]["path_args"] == (
        UUID("11111111-1111-4111-8111-111111111111"),
        "gmail",
        "GMAIL_SEND_EMAIL",
    )
    assert transport.calls[0]["body"] == {
        "payload": {"to": "a@example.com", "subject": "Hi"}
    }


def test_connectors_triggers_list_uses_org_and_auth_config_path_args():
    transport = StubTransport()
    lemma = Lemma(
        token="token",
        base_url="https://api.example.test",
        org_id="11111111-1111-4111-8111-111111111111",
    )
    lemma._transport = transport

    lemma.connectors.triggers.list("work-outlook", search="message", limit=5)

    assert transport.calls[0]["endpoint"].endswith("connector_trigger_list")
    assert transport.calls[0]["path_args"] == (
        UUID("11111111-1111-4111-8111-111111111111"),
        "work-outlook",
    )
    assert transport.calls[0]["kwargs"] == {
        "search": "message",
        "limit": 5,
    }


def test_connectors_trigger_get_uses_org_auth_config_and_trigger():
    transport = StubTransport()
    lemma = Lemma(
        token="token",
        base_url="https://api.example.test",
        org_id="11111111-1111-4111-8111-111111111111",
    )
    lemma._transport = transport

    lemma.connectors.triggers.get("work-outlook", "outlook:composio:new_message")

    assert transport.calls[0]["endpoint"].endswith("connector_trigger_get")
    assert transport.calls[0]["path_args"] == (
        UUID("11111111-1111-4111-8111-111111111111"),
        "work-outlook",
        "outlook:composio:new_message",
    )


def test_pod_surfaces_use_generated_models():
    transport = StubTransport()
    pod = Pod(
        "22222222-2222-4222-8222-222222222222",
        org_id="11111111-1111-4111-8111-111111111111",
        token="token",
        base_url="https://api.example.test",
    )
    pod._transport = transport
    pod.surfaces._transport = transport

    # create provisions a surface (name defaults to the lowercased platform).
    pod.surfaces.create(
        {
            "platform": "SLACK",
            "default_agent_name": "triage",
            "credential_mode": "SYSTEM",
            "account_id": "33333333-3333-4333-8333-333333333333",
            "config": {
                "channels": [
                    {"channel_id": "C123", "agent_name": "triage"}
                ]
            },
        }
    )
    assert transport.calls[0]["endpoint"].endswith("agent_surface_create")
    assert transport.calls[0]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
    )
    assert transport.calls[0]["body_model"] == "SurfaceCreateRequest"

    # update patches an existing surface addressed by its pod-unique name.
    pod.surfaces.update("slack", {"is_enabled": False})
    assert transport.calls[1]["endpoint"].endswith("agent_surface_update")
    assert transport.calls[1]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
        "slack",
    )
    assert transport.calls[1]["body_model"] == "SurfaceUpdateRequest"

    # setup merges status + admin-consent + checklist into one read.
    pod.surfaces.setup("slack")
    assert transport.calls[2]["endpoint"].endswith("agent_surface_setup")
    assert transport.calls[2]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
        "slack",
    )

    pod.surfaces.start_telegram_bot_setup(
        {
            "name": "telegram-support",
            "default_agent_name": "triage",
        }
    )
    assert transport.calls[3]["endpoint"].endswith(
        "agent_surface_telegram_managed_start"
    )
    assert transport.calls[3]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
    )
    assert (
        transport.calls[3]["body_model"]
        == "TelegramManagedBotSetupRequest"
    )

    pod.surfaces.get_telegram_bot_setup("setup-123")
    assert transport.calls[4]["endpoint"].endswith(
        "agent_surface_telegram_managed_get"
    )
    assert transport.calls[4]["path_args"] == (
        UUID("22222222-2222-4222-8222-222222222222"),
        "setup-123",
    )


def test_user_update_profile_uses_generated_model():
    transport = StubTransport()
    lemma = Lemma(
        token="token",
        base_url="https://api.example.test",
        org_id="11111111-1111-4111-8111-111111111111",
    )
    lemma._transport = transport
    lemma.user._transport = transport

    lemma.user.update_profile(
        {"mobile_number": "+15551234567", "telegram_username": "surfaceuser"}
    )

    assert transport.calls[0]["endpoint"].endswith("user_profile_upsert")
    assert transport.calls[0]["path_args"] == ()
    assert transport.calls[0]["body_model"] == "UserProfileRequest"
    assert transport.calls[0]["body"] == {
        "mobile_number": "+15551234567",
        "telegram_username": "surfaceuser",
    }


def test_pod_from_env_requires_pod_id(monkeypatch, tmp_path):
    monkeypatch.setenv("LEMMA_TOKEN", "token")
    monkeypatch.setenv("LEMMA_BASE_URL", "https://api.example.test")
    monkeypatch.delenv("LEMMA_POD_ID", raising=False)

    with pytest.raises(LemmaConfigError):
        Pod.from_env(config_path=tmp_path / "empty-config.json")


def test_transport_raises_typed_api_error():
    error = LemmaTransport._error_from_response(
        LemmaTransport.__new__(LemmaTransport),
        400,
        None,
        b'{"message":"bad request","code":"BAD","details":{"field":"x"}}',
    )

    assert isinstance(error, LemmaAPIError)
    assert error.status_code == 400
    assert error.code == "BAD"
    assert error.details == {"field": "x"}
