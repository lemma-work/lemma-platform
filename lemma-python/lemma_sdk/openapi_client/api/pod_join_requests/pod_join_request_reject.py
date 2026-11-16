from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.pod_join_request_create_response import PodJoinRequestCreateResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    join_request_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/join-requests/{join_request_id}/reject".format(
            pod_id=quote(str(pod_id), safe=""),
            join_request_id=quote(str(join_request_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PodJoinRequestCreateResponse | None:
    if response.status_code == 200:
        response_200 = PodJoinRequestCreateResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PodJoinRequestCreateResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    join_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PodJoinRequestCreateResponse]:
    """Reject Pod Join Request

     Turn down a pending pod join request; the asker is told by email

    Args:
        pod_id (UUID):
        join_request_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodJoinRequestCreateResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        join_request_id=join_request_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    join_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PodJoinRequestCreateResponse | None:
    """Reject Pod Join Request

     Turn down a pending pod join request; the asker is told by email

    Args:
        pod_id (UUID):
        join_request_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodJoinRequestCreateResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        join_request_id=join_request_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    join_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PodJoinRequestCreateResponse]:
    """Reject Pod Join Request

     Turn down a pending pod join request; the asker is told by email

    Args:
        pod_id (UUID):
        join_request_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodJoinRequestCreateResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        join_request_id=join_request_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    join_request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PodJoinRequestCreateResponse | None:
    """Reject Pod Join Request

     Turn down a pending pod join request; the asker is told by email

    Args:
        pod_id (UUID):
        join_request_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodJoinRequestCreateResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            join_request_id=join_request_id,
            client=client,
        )
    ).parsed
