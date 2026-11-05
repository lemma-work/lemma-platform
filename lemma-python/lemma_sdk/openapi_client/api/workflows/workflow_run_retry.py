from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.workflow_run_response import WorkflowRunResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    run_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/workflow-runs/{run_id}/retry".format(
            pod_id=quote(str(pod_id), safe=""),
            run_id=quote(str(run_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WorkflowRunResponse | None:
    if response.status_code == 200:
        response_200 = WorkflowRunResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | WorkflowRunResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | WorkflowRunResponse]:
    """Retry Workflow Run

     Resume a failed run from the node that failed, keeping every earlier node's output -- an agent
    step's answer is not produced again. Allowed when the failed node is a function, decision, loop or
    wait-until node; any other run, or one that has not failed, returns 409.

    Args:
        pod_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkflowRunResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        run_id=run_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | WorkflowRunResponse | None:
    """Retry Workflow Run

     Resume a failed run from the node that failed, keeping every earlier node's output -- an agent
    step's answer is not produced again. Allowed when the failed node is a function, decision, loop or
    wait-until node; any other run, or one that has not failed, returns 409.

    Args:
        pod_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkflowRunResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        run_id=run_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | WorkflowRunResponse]:
    """Retry Workflow Run

     Resume a failed run from the node that failed, keeping every earlier node's output -- an agent
    step's answer is not produced again. Allowed when the failed node is a function, decision, loop or
    wait-until node; any other run, or one that has not failed, returns 409.

    Args:
        pod_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkflowRunResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        run_id=run_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | WorkflowRunResponse | None:
    """Retry Workflow Run

     Resume a failed run from the node that failed, keeping every earlier node's output -- an agent
    step's answer is not produced again. Allowed when the failed node is a function, decision, loop or
    wait-until node; any other run, or one that has not failed, returns 409.

    Args:
        pod_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkflowRunResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            run_id=run_id,
            client=client,
        )
    ).parsed
