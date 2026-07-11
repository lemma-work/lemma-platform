from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    AuthProvider,
)
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
)

pytestmark = pytest.mark.asyncio


async def test_list_operations_reads_from_catalog():
    connector_repository = AsyncMock(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="slack",
                auth_provider=AuthProvider.LEMMA,
            )
        )
    )
    operation_repository = AsyncMock()
    created_operation = ConnectorOperationEntity(
        id="slack:send_message",
        connector_id="slack",
        name="send_message",
        provider_operation_name="send_message",
        description="Send a Slack message",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    operation_repository.list_by_connector.return_value = [created_operation]

    service = ConnectorOperationService(
        connector_repository=connector_repository,
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(),
        account_resolution_service=AsyncMock(),
    )

    operations = await service.list_operations("slack")

    assert [operation.name for operation in operations] == ["send_message"]
    operation_repository.list_by_connector.assert_awaited_once_with(
        "slack",
        search_query=None,
        limit=None,
    )


async def test_discover_operations_returns_structured_summary():
    connector_repository = AsyncMock(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="gmail",
                title="Gmail",
                description="Email connector",
                auth_provider=AuthProvider.COMPOSIO,
            )
        )
    )
    operation_repository = AsyncMock()
    operation_repository.list_by_connector.return_value = [
        ConnectorOperationEntity(
            id="gmail:send_message",
            connector_id="gmail",
            name="send_message",
            provider_operation_name="send_message",
            description="Send an email message to one or more recipients.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
        ConnectorOperationEntity(
            id="gmail:list_messages",
            connector_id="gmail",
            name="list_messages",
            provider_operation_name="list_messages",
            description="List messages from the mailbox.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
    ]

    service = ConnectorOperationService(
        connector_repository=connector_repository,
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(),
        account_resolution_service=AsyncMock(),
    )

    response = await service.discover_operations("gmail")

    assert response.connector_id == "gmail"
    assert response.total_operations == 2
    assert response.returned_count == 2
    assert [item.name for item in response.items] == [
        "send_message",
        "list_messages",
    ]
    assert response.items[0].description.startswith("Send an email message")


async def test_discover_operations_uses_repository_search_for_queries():
    connector_repository = AsyncMock(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="gmail",
                auth_provider=AuthProvider.COMPOSIO,
            )
        )
    )
    operation_repository = AsyncMock()
    operation_repository.list_by_connector.side_effect = [
        [
            ConnectorOperationEntity(
                id="gmail:messages_send",
                connector_id="gmail",
                name="messages_send",
                provider_operation_name="messages_send",
                description="Send an email message to recipients.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            ),
            ConnectorOperationEntity(
                id="gmail:messages_list",
                connector_id="gmail",
                name="messages_list",
                provider_operation_name="messages_list",
                description="List messages from a mailbox.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            ),
        ],
        [
            ConnectorOperationEntity(
                id="gmail:messages_send",
                connector_id="gmail",
                name="messages_send",
                provider_operation_name="messages_send",
                description="Send an email message to recipients.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )
        ],
    ]

    service = ConnectorOperationService(
        connector_repository=connector_repository,
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(),
        account_resolution_service=AsyncMock(),
    )

    response = await service.discover_operations(
        "gmail",
        query="send an email",
        limit=1,
    )

    assert response.returned_count == 1
    assert response.items[0].name == "messages_send"
    assert operation_repository.list_by_connector.await_args_list[0].args == (
        "gmail",
    )
    assert operation_repository.list_by_connector.await_args_list[0].kwargs == {
        "search_query": None,
        "limit": None,
    }
    assert operation_repository.list_by_connector.await_args_list[1].args == (
        "gmail",
    )
    assert operation_repository.list_by_connector.await_args_list[1].kwargs == {
        "search_query": "send an email",
        "limit": 1,
    }


async def test_get_operation_details_batch_returns_all_when_names_omitted():
    operation_repository = AsyncMock()
    operation_repository.list_by_connector.return_value = [
        ConnectorOperationEntity(
            id="slack:channels_list",
            connector_id="slack",
            name="channels_list",
            provider_operation_name="channels_list",
            description="List channels.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
        ConnectorOperationEntity(
            id="slack:messages_post",
            connector_id="slack",
            name="messages_post",
            provider_operation_name="messages_post",
            description="Post a message.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
    ]

    service = ConnectorOperationService(
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=ConnectorEntity(
                    id="slack",
                    auth_provider=AuthProvider.LEMMA,
                )
            )
        ),
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(),
        account_resolution_service=AsyncMock(),
    )

    response = await service.get_operation_details_batch("slack")

    assert response.connector_id == "slack"
    assert response.returned_count == 2
    assert [item.name for item in response.items] == [
        "channels_list",
        "messages_post",
    ]


async def test_get_operation_details_batch_matches_names_case_insensitively():
    operation_repository = AsyncMock()
    operation_repository.list_by_connector.return_value = [
        ConnectorOperationEntity(
            id="excel:EXCEL_CREATE_WORKBOOK",
            connector_id="excel",
            name="EXCEL_CREATE_WORKBOOK",
            provider_operation_name="EXCEL_CREATE_WORKBOOK",
            description="Create a workbook.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
    ]

    service = ConnectorOperationService(
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=ConnectorEntity(
                    id="excel",
                    auth_provider=AuthProvider.COMPOSIO,
                )
            )
        ),
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(),
        account_resolution_service=AsyncMock(),
    )

    response = await service.get_operation_details_batch(
        "excel",
        operation_names=["excel_create_workbook"],
    )

    assert response.returned_count == 1
    assert response.items[0].name == "EXCEL_CREATE_WORKBOOK"


async def test_discover_operations_includes_relevance_score_for_queries():
    connector_repository = AsyncMock(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="excel",
                auth_provider=AuthProvider.COMPOSIO,
            )
        )
    )
    operation_repository = AsyncMock()
    operation_repository.list_by_connector.side_effect = [
        [
            ConnectorOperationEntity(
                id="excel:EXCEL_CREATE_WORKBOOK",
                connector_id="excel",
                name="EXCEL_CREATE_WORKBOOK",
                provider_operation_name="EXCEL_CREATE_WORKBOOK",
                description="Create a new Excel workbook.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )
        ],
        [
            ConnectorOperationEntity(
                id="excel:EXCEL_CREATE_WORKBOOK",
                connector_id="excel",
                name="EXCEL_CREATE_WORKBOOK",
                provider_operation_name="EXCEL_CREATE_WORKBOOK",
                description="Create a new Excel workbook.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )
        ],
    ]

    service = ConnectorOperationService(
        connector_repository=connector_repository,
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(),
        account_resolution_service=AsyncMock(),
    )

    response = await service.discover_operations(
        "excel",
        query="create workbook",
    )

    assert response.items[0].relevance_score is not None
    assert response.items[0].relevance_score > 0


async def test_execute_operation_uses_provider_operation_name():
    operation_repository = AsyncMock()
    operation_repository.has_operations.return_value = True
    operation_repository.get_by_connector_and_name.return_value = (
        ConnectorOperationEntity(
            id="gmail:gmail_send_email",
            connector_id="gmail",
            name="gmail_send_email",
            provider_operation_name="GMAIL_SEND_EMAIL",
            description="Send email",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )

    account = SimpleNamespace(id=uuid4(), credentials={"access_token": "token"})
    account_resolution_service = AsyncMock(
        resolve_account=AsyncMock(return_value=account)
    )
    operation_gateway = AsyncMock(
        execute_operation=AsyncMock(return_value={"ok": True})
    )

    service = ConnectorOperationService(
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=ConnectorEntity(
                    id="gmail",
                    auth_provider=AuthProvider.COMPOSIO,
                )
            )
        ),
        operation_repository=operation_repository,
        operation_gateway=operation_gateway,
        account_resolution_service=account_resolution_service,
    )

    response = await service.execute_operation(
        connector_id="gmail",
        operation_name="gmail_send_email",
        payload={"subject": "Hello"},
        user_id=uuid4(),
    )

    assert response.result == {"ok": True}
    operation_gateway.execute_operation.assert_awaited_once()
    assert (
        operation_gateway.execute_operation.await_args.kwargs["operation_name"]
        == "GMAIL_SEND_EMAIL"
    )


async def test_execute_operation_wraps_unexpected_errors_in_domain_error():
    operation_repository = AsyncMock()
    operation_repository.get_by_connector_and_name.return_value = (
        ConnectorOperationEntity(
            id="slack:send_message",
            connector_id="slack",
            name="send_message",
            provider_operation_name="send_message",
            description="Send a message",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )

    account = SimpleNamespace(id=uuid4(), credentials={"access_token": "token"})
    service = ConnectorOperationService(
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=ConnectorEntity(
                    id="slack",
                    auth_provider=AuthProvider.LEMMA,
                )
            )
        ),
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(
            execute_operation=AsyncMock(side_effect=RuntimeError("provider exploded"))
        ),
        account_resolution_service=AsyncMock(
            resolve_account=AsyncMock(return_value=account)
        ),
    )

    with pytest.raises(OperationExecutionInfrastructureError) as exc_info:
        await service.execute_operation(
            connector_id="slack",
            operation_name="send_message",
            payload={"text": "hi"},
            user_id=uuid4(),
        )

    assert exc_info.value.code == "OPERATION_EXECUTION_INFRA_ERROR"
    assert exc_info.value.details == {"error_type": "RuntimeError"}
    assert "provider exploded" not in str(exc_info.value)


class _BinaryResult(BaseModel):
    type: str = "binary_content"
    content_base64: str
    media_type: str
    size_bytes: int


async def test_execute_operation_normalizes_pydantic_binary_results():
    operation_repository = AsyncMock()
    operation_repository.get_by_connector_and_name.return_value = (
        ConnectorOperationEntity(
            id="google_drive:files_export",
            connector_id="google_drive",
            name="files_export",
            provider_operation_name="files_export",
            description="Export a file",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )

    account = SimpleNamespace(id=uuid4(), credentials={"access_token": "token"})
    service = ConnectorOperationService(
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=ConnectorEntity(
                    id="google_drive",
                    auth_provider=AuthProvider.COMPOSIO,
                )
            )
        ),
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(
            execute_operation=AsyncMock(
                return_value=_BinaryResult(
                    content_base64="aGVsbG8=",
                    media_type="text/plain",
                    size_bytes=5,
                )
            )
        ),
        account_resolution_service=AsyncMock(
            resolve_account=AsyncMock(return_value=account)
        ),
    )

    response = await service.execute_operation(
        connector_id="google_drive",
        operation_name="files_export",
        payload={"file_id": "123", "mime_type": "text/plain"},
        user_id=uuid4(),
    )

    assert response.result == {
        "type": "binary_content",
        "content_base64": "aGVsbG8=",
        "media_type": "text/plain",
        "size_bytes": 5,
    }
