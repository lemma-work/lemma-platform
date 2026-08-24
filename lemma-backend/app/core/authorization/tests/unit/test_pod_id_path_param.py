"""A pod-scoped route says 400 for a malformed pod_id, not 500.

``get_pod_context`` reads ``pod_id`` off the path and converts it. The missing
case was handled; the unparseable one was not, so every pod-scoped route — and
there are dozens — answered a server error for a request that was simply
malformed, with a traceback in the body wherever debug was on. Found by a
scenario that passed a pod's *name* where its id belonged.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from app.core.authorization.dependencies import get_pod_context


def _request(pod_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        path_params={} if pod_id is None else {"pod_id": pod_id},
        query_params={},
        headers={},
        state=SimpleNamespace(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "orders_scn7f3a1",  # a pod name, which is what a caller reaches for
        "",
        "01a02f04-56a7-7399-b0c3",  # a uuid cut short
        "../../etc/passwd",
    ],
)
async def test_a_pod_id_that_is_not_a_uuid_is_a_bad_request(raw: str) -> None:
    with pytest.raises(HTTPException) as raised:
        await get_pod_context(
            request=_request(raw),
            user=SimpleNamespace(id=uuid4()),
            uow=SimpleNamespace(),
        )
    assert raised.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_a_missing_pod_id_is_still_a_bad_request() -> None:
    """The case that was already handled, kept honest beside the new one."""
    with pytest.raises(HTTPException) as raised:
        await get_pod_context(
            request=_request(None),
            user=SimpleNamespace(id=uuid4()),
            uow=SimpleNamespace(),
        )
    assert raised.value.status_code == status.HTTP_400_BAD_REQUEST
