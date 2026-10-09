from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.web_widget_response import WebWidgetResponse
from ...models.web_widget_update_request import WebWidgetUpdateRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    widget_id: UUID,
    *,
    body: WebWidgetUpdateRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/pods/{pod_id}/web-widgets/{widget_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            widget_id=quote(str(widget_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WebWidgetResponse | None:
    if response.status_code == 200:
        response_200 = WebWidgetResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | WebWidgetResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    widget_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: WebWidgetUpdateRequest,
) -> Response[ErrorResponse | WebWidgetResponse]:
    """Update Widget

    Args:
        pod_id (UUID):
        widget_id (UUID):
        body (WebWidgetUpdateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebWidgetResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        widget_id=widget_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    widget_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: WebWidgetUpdateRequest,
) -> ErrorResponse | WebWidgetResponse | None:
    """Update Widget

    Args:
        pod_id (UUID):
        widget_id (UUID):
        body (WebWidgetUpdateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebWidgetResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        widget_id=widget_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    widget_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: WebWidgetUpdateRequest,
) -> Response[ErrorResponse | WebWidgetResponse]:
    """Update Widget

    Args:
        pod_id (UUID):
        widget_id (UUID):
        body (WebWidgetUpdateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebWidgetResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        widget_id=widget_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    widget_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: WebWidgetUpdateRequest,
) -> ErrorResponse | WebWidgetResponse | None:
    """Update Widget

    Args:
        pod_id (UUID):
        widget_id (UUID):
        body (WebWidgetUpdateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebWidgetResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            widget_id=widget_id,
            client=client,
            body=body,
        )
    ).parsed
