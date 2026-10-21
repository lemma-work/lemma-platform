from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.slack_app_create_request import SlackAppCreateRequest
from ...models.slack_app_create_response import SlackAppCreateResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    body: SlackAppCreateRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/surface-setup/slack/apps".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SlackAppCreateResponse | None:
    if response.status_code == 200:
        response_200 = SlackAppCreateResponse.from_dict(response.json())

        return response_200

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
) -> Response[ErrorResponse | SlackAppCreateResponse]:
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
    body: SlackAppCreateRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | SlackAppCreateResponse]:
    """Create Slack App

     Create an agent's own Slack app, named after it. What is left is
    authorising it.

    One app per agent per workspace: until it is authorised, asking again
    answers with the same app, and so does the same `Idempotency-Key` from the
    same member for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (SlackAppCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackAppCreateResponse]
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
    body: SlackAppCreateRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | SlackAppCreateResponse | None:
    """Create Slack App

     Create an agent's own Slack app, named after it. What is left is
    authorising it.

    One app per agent per workspace: until it is authorised, asking again
    answers with the same app, and so does the same `Idempotency-Key` from the
    same member for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (SlackAppCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackAppCreateResponse
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
    body: SlackAppCreateRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | SlackAppCreateResponse]:
    """Create Slack App

     Create an agent's own Slack app, named after it. What is left is
    authorising it.

    One app per agent per workspace: until it is authorised, asking again
    answers with the same app, and so does the same `Idempotency-Key` from the
    same member for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (SlackAppCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackAppCreateResponse]
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
    body: SlackAppCreateRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | SlackAppCreateResponse | None:
    """Create Slack App

     Create an agent's own Slack app, named after it. What is left is
    authorising it.

    One app per agent per workspace: until it is authorised, asking again
    answers with the same app, and so does the same `Idempotency-Key` from the
    same member for a day.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (SlackAppCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackAppCreateResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed
