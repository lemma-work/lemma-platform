from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.home_seen_request import HomeSeenRequest
from ...models.home_seen_response import HomeSeenResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    organization_id: UUID,
    *,
    body: HomeSeenRequest | None | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/organizations/{organization_id}/home/seen".format(
            organization_id=quote(str(organization_id), safe=""),
        ),
    }

    if isinstance(body, HomeSeenRequest):
        _kwargs["json"] = body.to_dict()
    else:
        _kwargs["json"] = body

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | HomeSeenResponse | None:
    if response.status_code == 200:
        response_200 = HomeSeenResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | HomeSeenResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: HomeSeenRequest | None | Unset = UNSET,
) -> Response[ErrorResponse | HomeSeenResponse]:
    """Mark Home Seen

     Mark the organization's Home seen by the current user, as of `seen_at` (now when omitted; a future
    moment is read as now). The mark only moves forward, so an older tab cannot undo a newer one.
    Returns the mark and what it was before.

    Args:
        organization_id (UUID):
        body (HomeSeenRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HomeSeenResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: HomeSeenRequest | None | Unset = UNSET,
) -> ErrorResponse | HomeSeenResponse | None:
    """Mark Home Seen

     Mark the organization's Home seen by the current user, as of `seen_at` (now when omitted; a future
    moment is read as now). The mark only moves forward, so an older tab cannot undo a newer one.
    Returns the mark and what it was before.

    Args:
        organization_id (UUID):
        body (HomeSeenRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HomeSeenResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: HomeSeenRequest | None | Unset = UNSET,
) -> Response[ErrorResponse | HomeSeenResponse]:
    """Mark Home Seen

     Mark the organization's Home seen by the current user, as of `seen_at` (now when omitted; a future
    moment is read as now). The mark only moves forward, so an older tab cannot undo a newer one.
    Returns the mark and what it was before.

    Args:
        organization_id (UUID):
        body (HomeSeenRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HomeSeenResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: HomeSeenRequest | None | Unset = UNSET,
) -> ErrorResponse | HomeSeenResponse | None:
    """Mark Home Seen

     Mark the organization's Home seen by the current user, as of `seen_at` (now when omitted; a future
    moment is read as now). The mark only moves forward, so an older tab cannot undo a newer one.
    Returns the mark and what it was before.

    Args:
        organization_id (UUID):
        body (HomeSeenRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HomeSeenResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            client=client,
            body=body,
        )
    ).parsed
