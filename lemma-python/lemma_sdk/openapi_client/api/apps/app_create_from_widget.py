from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.app_detail_response import AppDetailResponse
from ...models.create_app_from_widget_request import CreateAppFromWidgetRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: CreateAppFromWidgetRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/apps/from-widget".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AppDetailResponse | ErrorResponse | None:
    if response.status_code == 201:
        response_201 = AppDetailResponse.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AppDetailResponse | ErrorResponse]:
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
    body: CreateAppFromWidgetRequest,
) -> Response[AppDetailResponse | ErrorResponse]:
    """Save Widget As App

     Promote a conversation widget into a persisted app.

    The widget and the app share one source artifact at two lifecycle stages. This
    fetches the stored fragment, preserves it, and deploys it in the standalone
    document wrapper (without conversation-only padding or height messaging).

    Args:
        pod_id (UUID):
        body (CreateAppFromWidgetRequest): Promote a conversation widget into a persisted app.

            The widget's stored source fragment (addressed by conversation + tool call) is
            preserved, wrapped into a standalone document without embed-only chrome, and
            deployed as the app's bundle.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppDetailResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CreateAppFromWidgetRequest,
) -> AppDetailResponse | ErrorResponse | None:
    """Save Widget As App

     Promote a conversation widget into a persisted app.

    The widget and the app share one source artifact at two lifecycle stages. This
    fetches the stored fragment, preserves it, and deploys it in the standalone
    document wrapper (without conversation-only padding or height messaging).

    Args:
        pod_id (UUID):
        body (CreateAppFromWidgetRequest): Promote a conversation widget into a persisted app.

            The widget's stored source fragment (addressed by conversation + tool call) is
            preserved, wrapped into a standalone document without embed-only chrome, and
            deployed as the app's bundle.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppDetailResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CreateAppFromWidgetRequest,
) -> Response[AppDetailResponse | ErrorResponse]:
    """Save Widget As App

     Promote a conversation widget into a persisted app.

    The widget and the app share one source artifact at two lifecycle stages. This
    fetches the stored fragment, preserves it, and deploys it in the standalone
    document wrapper (without conversation-only padding or height messaging).

    Args:
        pod_id (UUID):
        body (CreateAppFromWidgetRequest): Promote a conversation widget into a persisted app.

            The widget's stored source fragment (addressed by conversation + tool call) is
            preserved, wrapped into a standalone document without embed-only chrome, and
            deployed as the app's bundle.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppDetailResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CreateAppFromWidgetRequest,
) -> AppDetailResponse | ErrorResponse | None:
    """Save Widget As App

     Promote a conversation widget into a persisted app.

    The widget and the app share one source artifact at two lifecycle stages. This
    fetches the stored fragment, preserves it, and deploys it in the standalone
    document wrapper (without conversation-only padding or height messaging).

    Args:
        pod_id (UUID):
        body (CreateAppFromWidgetRequest): Promote a conversation widget into a persisted app.

            The widget's stored source fragment (addressed by conversation + tool call) is
            preserved, wrapped into a standalone document without embed-only chrome, and
            deployed as the app's bundle.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppDetailResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed
