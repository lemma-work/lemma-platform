from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.follow_up_request import FollowUpRequest
from ...models.follow_up_response import FollowUpResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    contact_id: UUID,
    *,
    body: FollowUpRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/contacts/{contact_id}/messages".format(
            pod_id=quote(str(pod_id), safe=""),
            contact_id=quote(str(contact_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FollowUpResponse | None:
    if response.status_code == 200:
        response_200 = FollowUpResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | FollowUpResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FollowUpRequest,
) -> Response[ErrorResponse | FollowUpResponse]:
    """Follow Up Contact

     Write to a contact in their most recent conversation, where the channel allows.

    Refused (409) when they unsubscribed there, when WhatsApp's 24-hour window
    has closed, or when they have never written to the pod; 429 past the day's
    follow-ups for this contact; 502 when the platform did not take it, which
    the conversation then shows as not sent.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        body (FollowUpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FollowUpResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        contact_id=contact_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FollowUpRequest,
) -> ErrorResponse | FollowUpResponse | None:
    """Follow Up Contact

     Write to a contact in their most recent conversation, where the channel allows.

    Refused (409) when they unsubscribed there, when WhatsApp's 24-hour window
    has closed, or when they have never written to the pod; 429 past the day's
    follow-ups for this contact; 502 when the platform did not take it, which
    the conversation then shows as not sent.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        body (FollowUpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FollowUpResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        contact_id=contact_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FollowUpRequest,
) -> Response[ErrorResponse | FollowUpResponse]:
    """Follow Up Contact

     Write to a contact in their most recent conversation, where the channel allows.

    Refused (409) when they unsubscribed there, when WhatsApp's 24-hour window
    has closed, or when they have never written to the pod; 429 past the day's
    follow-ups for this contact; 502 when the platform did not take it, which
    the conversation then shows as not sent.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        body (FollowUpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FollowUpResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        contact_id=contact_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FollowUpRequest,
) -> ErrorResponse | FollowUpResponse | None:
    """Follow Up Contact

     Write to a contact in their most recent conversation, where the channel allows.

    Refused (409) when they unsubscribed there, when WhatsApp's 24-hour window
    has closed, or when they have never written to the pod; 429 past the day's
    follow-ups for this contact; 502 when the platform did not take it, which
    the conversation then shows as not sent.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        body (FollowUpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FollowUpResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            contact_id=contact_id,
            client=client,
            body=body,
        )
    ).parsed
