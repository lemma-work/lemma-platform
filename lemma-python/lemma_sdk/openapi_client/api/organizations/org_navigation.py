from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.navigation_response import NavigationResponse
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/navigation",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> NavigationResponse | None:
    if response.status_code == 200:
        response_200 = NavigationResponse.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[NavigationResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[NavigationResponse]:
    """List Organizations And Their Pods

     Every organization the current user belongs to, each with the pods they can see in it. Replaces
    fetching the organization list and then one pod list per organization; the payload stays shallow so
    it does not grow with the contents of each pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[NavigationResponse]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> NavigationResponse | None:
    """List Organizations And Their Pods

     Every organization the current user belongs to, each with the pods they can see in it. Replaces
    fetching the organization list and then one pod list per organization; the payload stays shallow so
    it does not grow with the contents of each pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        NavigationResponse
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[NavigationResponse]:
    """List Organizations And Their Pods

     Every organization the current user belongs to, each with the pods they can see in it. Replaces
    fetching the organization list and then one pod list per organization; the payload stays shallow so
    it does not grow with the contents of each pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[NavigationResponse]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> NavigationResponse | None:
    """List Organizations And Their Pods

     Every organization the current user belongs to, each with the pods they can see in it. Replaces
    fetching the organization list and then one pod list per organization; the payload stays shallow so
    it does not grow with the contents of each pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        NavigationResponse
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
