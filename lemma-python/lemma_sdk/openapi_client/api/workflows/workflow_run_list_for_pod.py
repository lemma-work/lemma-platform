from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.workflow_run_list_response import WorkflowRunListResponse
from ...models.workflow_run_status import WorkflowRunStatus
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    limit: int | Unset = 50,
    status: list[WorkflowRunStatus] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_status: list[str] | None | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    elif isinstance(status, list):
        json_status = []
        for status_type_0_item_data in status:
            status_type_0_item = status_type_0_item_data.value
            json_status.append(status_type_0_item)

    else:
        json_status = status
    params["status"] = json_status

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/workflow-runs".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WorkflowRunListResponse | None:
    if response.status_code == 200:
        response_200 = WorkflowRunListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | WorkflowRunListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    status: list[WorkflowRunStatus] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | WorkflowRunListResponse]:
    """List Workflow Runs In Pod

     Recent runs across every workflow in the pod, newest first. Exists so an index that wants 'what has
    been happening here' makes one request instead of one per workflow. Filter with `status`
    (repeatable).

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 50.
        status (list[WorkflowRunStatus] | None | Unset):
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkflowRunListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        limit=limit,
        status=status,
        page_token=page_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    status: list[WorkflowRunStatus] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | WorkflowRunListResponse | None:
    """List Workflow Runs In Pod

     Recent runs across every workflow in the pod, newest first. Exists so an index that wants 'what has
    been happening here' makes one request instead of one per workflow. Filter with `status`
    (repeatable).

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 50.
        status (list[WorkflowRunStatus] | None | Unset):
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkflowRunListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        limit=limit,
        status=status,
        page_token=page_token,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    status: list[WorkflowRunStatus] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | WorkflowRunListResponse]:
    """List Workflow Runs In Pod

     Recent runs across every workflow in the pod, newest first. Exists so an index that wants 'what has
    been happening here' makes one request instead of one per workflow. Filter with `status`
    (repeatable).

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 50.
        status (list[WorkflowRunStatus] | None | Unset):
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkflowRunListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        limit=limit,
        status=status,
        page_token=page_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    status: list[WorkflowRunStatus] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | WorkflowRunListResponse | None:
    """List Workflow Runs In Pod

     Recent runs across every workflow in the pod, newest first. Exists so an index that wants 'what has
    been happening here' makes one request instead of one per workflow. Filter with `status`
    (repeatable).

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 50.
        status (list[WorkflowRunStatus] | None | Unset):
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkflowRunListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            limit=limit,
            status=status,
            page_token=page_token,
        )
    ).parsed
