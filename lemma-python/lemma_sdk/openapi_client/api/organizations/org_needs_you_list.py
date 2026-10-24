from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.needs_you_response import NeedsYouResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    organization_id: UUID,
    *,
    limit: int | Unset = 100,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/{organization_id}/needs-you".format(
            organization_id=quote(str(organization_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | NeedsYouResponse | None:
    if response.status_code == 200:
        response_200 = NeedsYouResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | NeedsYouResponse]:
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
    limit: int | Unset = 100,
) -> Response[ErrorResponse | NeedsYouResponse]:
    """List What Waits On You Across The Organization

     Everything waiting on the current user in every pod they may open in the organization, as one list,
    oldest first: questions, approvals and Agent Host permission requests in their own conversations
    (one item per conversation); workflow forms and decisions assigned to them; and contacts waiting on
    a person, in pods where they hold `channel.read` and `contact.read`. Each item names its pod and
    what to open: a conversation, a workflow run or a channel.

    Args:
        organization_id (UUID):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NeedsYouResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
) -> ErrorResponse | NeedsYouResponse | None:
    """List What Waits On You Across The Organization

     Everything waiting on the current user in every pod they may open in the organization, as one list,
    oldest first: questions, approvals and Agent Host permission requests in their own conversations
    (one item per conversation); workflow forms and decisions assigned to them; and contacts waiting on
    a person, in pods where they hold `channel.read` and `contact.read`. Each item names its pod and
    what to open: a conversation, a workflow run or a channel.

    Args:
        organization_id (UUID):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NeedsYouResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
) -> Response[ErrorResponse | NeedsYouResponse]:
    """List What Waits On You Across The Organization

     Everything waiting on the current user in every pod they may open in the organization, as one list,
    oldest first: questions, approvals and Agent Host permission requests in their own conversations
    (one item per conversation); workflow forms and decisions assigned to them; and contacts waiting on
    a person, in pods where they hold `channel.read` and `contact.read`. Each item names its pod and
    what to open: a conversation, a workflow run or a channel.

    Args:
        organization_id (UUID):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NeedsYouResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
) -> ErrorResponse | NeedsYouResponse | None:
    """List What Waits On You Across The Organization

     Everything waiting on the current user in every pod they may open in the organization, as one list,
    oldest first: questions, approvals and Agent Host permission requests in their own conversations
    (one item per conversation); workflow forms and decisions assigned to them; and contacts waiting on
    a person, in pods where they hold `channel.read` and `contact.read`. Each item names its pod and
    what to open: a conversation, a workflow run or a channel.

    Args:
        organization_id (UUID):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NeedsYouResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            client=client,
            limit=limit,
        )
    ).parsed
