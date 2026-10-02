from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.decider_response import DeciderResponse
from ...models.error_response import ErrorResponse
from ...models.update_decider_body import UpdateDeciderBody
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    decider_name: str,
    *,
    body: UpdateDeciderBody,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/pods/{pod_id}/deciders/{decider_name}".format(
            pod_id=quote(str(pod_id), safe=""),
            decider_name=quote(str(decider_name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DeciderResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DeciderResponse.from_dict(response.json())

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
) -> Response[DeciderResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateDeciderBody,
) -> Response[DeciderResponse | ErrorResponse]:
    """Save a new version of a decider

     Replace a decider's definition. The old version is kept, and decisions made with it still name it.

    Args:
        pod_id (UUID):
        decider_name (str):
        body (UpdateDeciderBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeciderResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decider_name=decider_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateDeciderBody,
) -> DeciderResponse | ErrorResponse | None:
    """Save a new version of a decider

     Replace a decider's definition. The old version is kept, and decisions made with it still name it.

    Args:
        pod_id (UUID):
        decider_name (str):
        body (UpdateDeciderBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeciderResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        decider_name=decider_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateDeciderBody,
) -> Response[DeciderResponse | ErrorResponse]:
    """Save a new version of a decider

     Replace a decider's definition. The old version is kept, and decisions made with it still name it.

    Args:
        pod_id (UUID):
        decider_name (str):
        body (UpdateDeciderBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeciderResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decider_name=decider_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateDeciderBody,
) -> DeciderResponse | ErrorResponse | None:
    """Save a new version of a decider

     Replace a decider's definition. The old version is kept, and decisions made with it still name it.

    Args:
        pod_id (UUID):
        decider_name (str):
        body (UpdateDeciderBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeciderResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            decider_name=decider_name,
            client=client,
            body=body,
        )
    ).parsed
