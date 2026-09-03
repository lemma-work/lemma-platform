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
    OperationExecutionError,
    OperationExecutionNotFoundError,
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

    def _send() -> ConnectorOperationEntity:
        return ConnectorOperationEntity(
            id="gmail:messages_send",
            connector_id="gmail",
            name="messages_send",
            provider_operation_name="messages_send",
            description="Send an email message to recipients.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

    def _list() -> ConnectorOperationEntity:
        return ConnectorOperationEntity(
            id="gmail:messages_list",
            connector_id="gmail",
            name="messages_list",
            provider_operation_name="messages_list",
            description="List messages from a mailbox.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

    # Answer on the arguments rather than on call order: the point of this test
    # is that a query reaches the repository's search, not the sequence the
    # service happens to read the total and the page in.
    async def _list_by_connector(
        _connector_id, *, search_query=None, limit=None, kind=None
    ):
        return [_send()] if search_query else [_send(), _list()]

    operation_repository.list_by_connector.side_effect = _list_by_connector
    operation_repository.count_by_connector.return_value = 2

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
    calls = operation_repository.list_by_connector.await_args_list
    assert calls[0].args == ("gmail",)
    assert calls[0].kwargs == {"search_query": "send an email", "limit": 1}
    # Exactly one listing. "Showing 1 of 2" is answered by a count query, not
    # by reading every row a second time and taking its length -- which is what
    # made one agent search across an org up to a hundred full table reads.
    assert len(calls) == 1
    assert response.total_operations == 2
    operation_repository.count_by_connector.assert_awaited_once_with("gmail", kind=None)


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


async def test_an_unnamed_details_batch_is_capped_and_says_so():
    """Each detail carries the operation's whole input and output schema, so
    "every operation" on a connector the size of Jira is tens of megabytes of
    JSON assembled in memory -- and the request schema documented that as the
    intended usage. `total_operations` is what tells the caller it was capped;
    without it the cap would be a silent truncation."""
    operation_repository = AsyncMock()
    operation_repository.list_by_connector.return_value = [
        ConnectorOperationEntity(
            id=f"slack:op_{index}",
            connector_id="slack",
            name=f"op_{index}",
            provider_operation_name=f"op_{index}",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        for index in range(5)
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

    response = await service.get_operation_details_batch("slack", limit=2)

    assert response.returned_count == 2
    assert response.total_operations == 5


async def test_naming_operations_still_returns_exactly_those():
    """The cap applies only to the unnamed case: a caller who names 300
    operations asked for 300, and the request schema bounds that list itself."""
    operation_repository = AsyncMock()
    operation_repository.list_by_connector.return_value = [
        ConnectorOperationEntity(
            id=f"slack:op_{index}",
            connector_id="slack",
            name=f"op_{index}",
            provider_operation_name=f"op_{index}",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        for index in range(5)
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

    response = await service.get_operation_details_batch(
        "slack", operation_names=["op_0", "op_3", "op_4"], limit=1
    )

    assert [item.name for item in response.items] == ["op_0", "op_3", "op_4"]


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
    # An error that is not transport-shaped is a fault on our side, so it is
    # not labelled as a provider outage -- that would invite a retry which
    # cannot succeed. It is still bounded: no traceback reaches the caller and
    # the upstream message is not reflected back.
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

    with pytest.raises(OperationExecutionError) as exc_info:
        await service.execute_operation(
            connector_id="slack",
            operation_name="send_message",
            payload={"text": "hi"},
            user_id=uuid4(),
        )

    assert exc_info.value.code == "OPERATION_EXECUTION_ERROR"
    assert exc_info.value.details == {"error_type": "RuntimeError"}
    assert "provider exploded" not in str(exc_info.value)


async def test_execute_operation_keeps_the_status_an_executor_reported():
    # The http/sql/mcp executors raise their own exception type carrying the
    # provider's status. Without honouring it, a resource that simply does not
    # exist would read as "our fault, 500" -- and a caller could not tell a repo
    # that was never created from a connector that is broken.
    class _HttpFailure(Exception):
        def __init__(self):
            super().__init__("GitHub said: Not Found for /repos/acme/crm")
            self.status_code = 404

    operation_repository = AsyncMock()
    operation_repository.get_by_connector_and_name.return_value = (
        ConnectorOperationEntity(
            id="github:repos_get",
            connector_id="github",
            name="repos_get",
            provider_operation_name="repos_get",
            input_schema={"type": "object"},
        )
    )
    account = SimpleNamespace(id=uuid4(), credentials={"access_token": "token"})
    service = ConnectorOperationService(
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=ConnectorEntity(
                    id="github",
                    auth_provider=AuthProvider.LEMMA,
                )
            )
        ),
        operation_repository=operation_repository,
        operation_gateway=AsyncMock(
            execute_operation=AsyncMock(side_effect=_HttpFailure())
        ),
        account_resolution_service=AsyncMock(
            resolve_account=AsyncMock(return_value=account)
        ),
    )

    with pytest.raises(OperationExecutionNotFoundError) as exc_info:
        await service.execute_operation(
            connector_id="github",
            operation_name="repos_get",
            payload={"owner": "acme", "repo": "crm"},
            user_id=uuid4(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.details == {
        "error_type": "_HttpFailure",
        "upstream_status": 404,
        # What the provider said. A status alone cannot tell a caller which of
        # the several things a 404 might mean actually happened.
        "upstream_message": "GitHub said: Not Found for /repos/acme/crm",
    }
    # The top-level message stays fixed and ours; the provider's words travel in
    # the details, where they are scrubbed of anything secret-shaped.
    assert "acme/crm" not in str(exc_info.value)


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
