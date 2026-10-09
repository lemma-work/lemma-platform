from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.open_table_request import OpenTableRequest
from ...models.table_opening_response import TableOpeningResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    table_name: str,
    *,
    body: OpenTableRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/pods/{pod_id}/datastore/tables/{table_name}/public-rows".format(
            pod_id=quote(str(pod_id), safe=""),
            table_name=quote(str(table_name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | TableOpeningResponse | None:
    if response.status_code == 200:
        response_200 = TableOpeningResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | TableOpeningResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OpenTableRequest,
) -> Response[ErrorResponse | TableOpeningResponse]:
    """Let People Outside Add Rows

     Open the table to rows from people outside the pod -- confirmed contacts, or anyone -- for the
    chosen columns only. Rows are added as the member opening it, who must be able to change the table.

    Args:
        pod_id (UUID):
        table_name (str):
        body (OpenTableRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TableOpeningResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OpenTableRequest,
) -> ErrorResponse | TableOpeningResponse | None:
    """Let People Outside Add Rows

     Open the table to rows from people outside the pod -- confirmed contacts, or anyone -- for the
    chosen columns only. Rows are added as the member opening it, who must be able to change the table.

    Args:
        pod_id (UUID):
        table_name (str):
        body (OpenTableRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TableOpeningResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        table_name=table_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OpenTableRequest,
) -> Response[ErrorResponse | TableOpeningResponse]:
    """Let People Outside Add Rows

     Open the table to rows from people outside the pod -- confirmed contacts, or anyone -- for the
    chosen columns only. Rows are added as the member opening it, who must be able to change the table.

    Args:
        pod_id (UUID):
        table_name (str):
        body (OpenTableRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TableOpeningResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OpenTableRequest,
) -> ErrorResponse | TableOpeningResponse | None:
    """Let People Outside Add Rows

     Open the table to rows from people outside the pod -- confirmed contacts, or anyone -- for the
    chosen columns only. Rows are added as the member opening it, who must be able to change the table.

    Args:
        pod_id (UUID):
        table_name (str):
        body (OpenTableRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TableOpeningResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            table_name=table_name,
            client=client,
            body=body,
        )
    ).parsed
