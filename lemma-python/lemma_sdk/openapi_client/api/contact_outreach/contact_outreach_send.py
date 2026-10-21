from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.outreach_request import OutreachRequest
from ...models.outreach_response import OutreachResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    contact_id: UUID,
    *,
    body: OutreachRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/contacts/{contact_id}/outreach".format(
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
) -> ErrorResponse | OutreachResponse | None:
    if response.status_code == 201:
        response_201 = OutreachResponse.from_dict(response.json())

        return response_201

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | OutreachResponse]:
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
    body: OutreachRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | OutreachResponse]:
    """Send Outreach

     Write to a contact first, on one of their handles: email any time,
    WhatsApp within 24 hours of their last message there. The line goes in
    their channel, at that handle's place -- opened if new -- and is sent from
    there. The same `client_message_id`, or `Idempotency-Key`, from the same
    member is the same line: a retry sends nothing new.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        idempotency_key (None | str | Unset):
        body (OutreachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutreachResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        contact_id=contact_id,
        body=body,
        idempotency_key=idempotency_key,
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
    body: OutreachRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | OutreachResponse | None:
    """Send Outreach

     Write to a contact first, on one of their handles: email any time,
    WhatsApp within 24 hours of their last message there. The line goes in
    their channel, at that handle's place -- opened if new -- and is sent from
    there. The same `client_message_id`, or `Idempotency-Key`, from the same
    member is the same line: a retry sends nothing new.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        idempotency_key (None | str | Unset):
        body (OutreachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutreachResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        contact_id=contact_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: OutreachRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | OutreachResponse]:
    """Send Outreach

     Write to a contact first, on one of their handles: email any time,
    WhatsApp within 24 hours of their last message there. The line goes in
    their channel, at that handle's place -- opened if new -- and is sent from
    there. The same `client_message_id`, or `Idempotency-Key`, from the same
    member is the same line: a retry sends nothing new.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        idempotency_key (None | str | Unset):
        body (OutreachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutreachResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        contact_id=contact_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: OutreachRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | OutreachResponse | None:
    """Send Outreach

     Write to a contact first, on one of their handles: email any time,
    WhatsApp within 24 hours of their last message there. The line goes in
    their channel, at that handle's place -- opened if new -- and is sent from
    there. The same `client_message_id`, or `Idempotency-Key`, from the same
    member is the same line: a retry sends nothing new.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        idempotency_key (None | str | Unset):
        body (OutreachRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutreachResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            contact_id=contact_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
