from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.workflow_run_response import WorkflowRunResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    workflow_name: str,
    *,
    idempotency_key: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/workflows/{workflow_name}/runs".format(
            pod_id=quote(str(pod_id), safe=""),
            workflow_name=quote(str(workflow_name), safe=""),
        ),
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WorkflowRunResponse | None:
    if response.status_code == 201:
        response_201 = WorkflowRunResponse.from_dict(response.json())

        return response_201

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
    workflow_name: str,
    *,
    client: AuthenticatedClient | Client,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | WorkflowRunResponse]:
    """Create Workflow Run

     Create a new run for this workflow. Takes no request body: if the workflow's entry node is a FORM
    node the run is created WAITING on it (see `active_wait` in the response) and input is submitted via
    `workflow.run.form.submit`; otherwise the run executes immediately. Trigger payloads for
    scheduled/event/datastore starts are supplied by the platform, not through this endpoint. Send an
    `Idempotency-Key` header to make a retry safe: the same key, from the same person, for the same
    workflow, returns the run it already created.

    Args:
        pod_id (UUID):
        workflow_name (str):
        idempotency_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkflowRunResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        workflow_name=workflow_name,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    workflow_name: str,
    *,
    client: AuthenticatedClient | Client,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | WorkflowRunResponse | None:
    """Create Workflow Run

     Create a new run for this workflow. Takes no request body: if the workflow's entry node is a FORM
    node the run is created WAITING on it (see `active_wait` in the response) and input is submitted via
    `workflow.run.form.submit`; otherwise the run executes immediately. Trigger payloads for
    scheduled/event/datastore starts are supplied by the platform, not through this endpoint. Send an
    `Idempotency-Key` header to make a retry safe: the same key, from the same person, for the same
    workflow, returns the run it already created.

    Args:
        pod_id (UUID):
        workflow_name (str):
        idempotency_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkflowRunResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        workflow_name=workflow_name,
        client=client,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    workflow_name: str,
    *,
    client: AuthenticatedClient | Client,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | WorkflowRunResponse]:
    """Create Workflow Run

     Create a new run for this workflow. Takes no request body: if the workflow's entry node is a FORM
    node the run is created WAITING on it (see `active_wait` in the response) and input is submitted via
    `workflow.run.form.submit`; otherwise the run executes immediately. Trigger payloads for
    scheduled/event/datastore starts are supplied by the platform, not through this endpoint. Send an
    `Idempotency-Key` header to make a retry safe: the same key, from the same person, for the same
    workflow, returns the run it already created.

    Args:
        pod_id (UUID):
        workflow_name (str):
        idempotency_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkflowRunResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        workflow_name=workflow_name,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    workflow_name: str,
    *,
    client: AuthenticatedClient | Client,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | WorkflowRunResponse | None:
    """Create Workflow Run

     Create a new run for this workflow. Takes no request body: if the workflow's entry node is a FORM
    node the run is created WAITING on it (see `active_wait` in the response) and input is submitted via
    `workflow.run.form.submit`; otherwise the run executes immediately. Trigger payloads for
    scheduled/event/datastore starts are supplied by the platform, not through this endpoint. Send an
    `Idempotency-Key` header to make a retry safe: the same key, from the same person, for the same
    workflow, returns the run it already created.

    Args:
        pod_id (UUID):
        workflow_name (str):
        idempotency_key (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkflowRunResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            workflow_name=workflow_name,
            client=client,
            idempotency_key=idempotency_key,
        )
    ).parsed
