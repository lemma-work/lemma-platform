from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.source_settings_response import SourceSettingsResponse
from ...models.whats_app_group_request import WhatsAppGroupRequest
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    body: WhatsAppGroupRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/channels/whatsapp-groups".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SourceSettingsResponse | None:
    if response.status_code == 201:
        response_201 = SourceSettingsResponse.from_dict(response.json())

        return response_201

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = ErrorResponse.from_dict(response.json())

        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | SourceSettingsResponse]:
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
    body: WhatsAppGroupRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | SourceSettingsResponse]:
    """Start Whatsapp Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    It is a new GROUP channel's source: the channel is made with it, and its
    id is `channel_id`. WhatsApp confirms the group moments later: it comes
    back ``pending``, and its invite link appears once confirmed. Telegram and
    Slack cannot create groups for a bot; add the bot to one of theirs instead.
    The same `Idempotency-Key` from the same member answers with the group it
    started, for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (WhatsAppGroupRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SourceSettingsResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: WhatsAppGroupRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | SourceSettingsResponse | None:
    """Start Whatsapp Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    It is a new GROUP channel's source: the channel is made with it, and its
    id is `channel_id`. WhatsApp confirms the group moments later: it comes
    back ``pending``, and its invite link appears once confirmed. Telegram and
    Slack cannot create groups for a bot; add the bot to one of theirs instead.
    The same `Idempotency-Key` from the same member answers with the group it
    started, for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (WhatsAppGroupRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SourceSettingsResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: WhatsAppGroupRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | SourceSettingsResponse]:
    """Start Whatsapp Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    It is a new GROUP channel's source: the channel is made with it, and its
    id is `channel_id`. WhatsApp confirms the group moments later: it comes
    back ``pending``, and its invite link appears once confirmed. Telegram and
    Slack cannot create groups for a bot; add the bot to one of theirs instead.
    The same `Idempotency-Key` from the same member answers with the group it
    started, for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (WhatsAppGroupRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SourceSettingsResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: WhatsAppGroupRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | SourceSettingsResponse | None:
    """Start Whatsapp Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    It is a new GROUP channel's source: the channel is made with it, and its
    id is `channel_id`. WhatsApp confirms the group moments later: it comes
    back ``pending``, and its invite link appears once confirmed. Telegram and
    Slack cannot create groups for a bot; add the bot to one of theirs instead.
    The same `Idempotency-Key` from the same member answers with the group it
    started, for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (WhatsAppGroupRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SourceSettingsResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
