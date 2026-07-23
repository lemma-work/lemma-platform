from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agentbox.adapters.workspace_runtime_client import (
    WorkspaceRuntimeClient,
    WorkspaceRuntimeError,
    WorkspaceRuntimeFileConflict,
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (404, WorkspaceRuntimeFileNotFound),
        (409, WorkspaceRuntimeFileConflict),
        (413, WorkspaceRuntimeFileRejected),
        (422, WorkspaceRuntimeFileRejected),
        (507, WorkspaceRuntimeFileRejected),
        (500, WorkspaceRuntimeError),
        (503, WorkspaceRuntimeError),
    ],
)
async def test_filesystem_http_statuses_are_mapped_without_hiding_outages(
    status_code: int,
    error_type: type[WorkspaceRuntimeError],
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    client = WorkspaceRuntimeClient("http://runtime", "secret")
    await client.close()
    client._client = httpx.AsyncClient(  # noqa: SLF001 - injected test transport
        base_url="http://runtime",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(error_type) as raised:
            await client.stat_file(
                "/workspace/missing",
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
            )
        if error_type is WorkspaceRuntimeFileRejected:
            assert raised.value.status_code == status_code
    finally:
        await client.close()
