from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.issued_hook_response import IssuedHookResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    name: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/datastore/hooks/{name}/rotate".format(
            pod_id=quote(str(pod_id), safe=""),
            name=quote(str(name), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | IssuedHookResponse | None:
    if response.status_code == 200:
        response_200 = IssuedHookResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | IssuedHookResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | IssuedHookResponse]:
    """Give A Hook A New Secret

     The old address stops working at once. The response carries the new secret and address; neither is
    shown again.

    Args:
        pod_id (UUID):
        name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | IssuedHookResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        name=name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | IssuedHookResponse | None:
    """Give A Hook A New Secret

     The old address stops working at once. The response carries the new secret and address; neither is
    shown again.

    Args:
        pod_id (UUID):
        name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | IssuedHookResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        name=name,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | IssuedHookResponse]:
    """Give A Hook A New Secret

     The old address stops working at once. The response carries the new secret and address; neither is
    shown again.

    Args:
        pod_id (UUID):
        name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | IssuedHookResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        name=name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | IssuedHookResponse | None:
    """Give A Hook A New Secret

     The old address stops working at once. The response carries the new secret and address; neither is
    shown again.

    Args:
        pod_id (UUID):
        name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | IssuedHookResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            name=name,
            client=client,
        )
    ).parsed
