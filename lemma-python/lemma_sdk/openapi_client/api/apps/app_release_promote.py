from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.app_detail_response import AppDetailResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    app_name: str,
    release_ref: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/apps/{app_name}/releases/{release_ref}/promote".format(
            pod_id=quote(str(pod_id), safe=""),
            app_name=quote(str(app_name), safe=""),
            release_ref=quote(str(release_ref), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AppDetailResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AppDetailResponse.from_dict(response.json())

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
) -> Response[AppDetailResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    app_name: str,
    release_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AppDetailResponse | ErrorResponse]:
    """Promote App Release

     Make an existing release the one this app serves. The release keeps its bytes; only the app's
    current-release pointer moves.

    Args:
        pod_id (UUID):
        app_name (str):
        release_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppDetailResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        app_name=app_name,
        release_ref=release_ref,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    app_name: str,
    release_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> AppDetailResponse | ErrorResponse | None:
    """Promote App Release

     Make an existing release the one this app serves. The release keeps its bytes; only the app's
    current-release pointer moves.

    Args:
        pod_id (UUID):
        app_name (str):
        release_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppDetailResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        app_name=app_name,
        release_ref=release_ref,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    app_name: str,
    release_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AppDetailResponse | ErrorResponse]:
    """Promote App Release

     Make an existing release the one this app serves. The release keeps its bytes; only the app's
    current-release pointer moves.

    Args:
        pod_id (UUID):
        app_name (str):
        release_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppDetailResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        app_name=app_name,
        release_ref=release_ref,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    app_name: str,
    release_ref: str,
    *,
    client: AuthenticatedClient | Client,
) -> AppDetailResponse | ErrorResponse | None:
    """Promote App Release

     Make an existing release the one this app serves. The release keeps its bytes; only the app's
    current-release pointer moves.

    Args:
        pod_id (UUID):
        app_name (str):
        release_ref (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppDetailResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            app_name=app_name,
            release_ref=release_ref,
            client=client,
        )
    ).parsed
