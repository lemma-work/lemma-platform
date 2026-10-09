from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.session_request import SessionRequest
from ...models.session_response import SessionResponse
from ...types import Response


def _get_kwargs(
    public_key: str,
    *,
    body: SessionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/public/web/{public_key}/session".format(
            public_key=quote(str(public_key), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SessionResponse | None:
    if response.status_code == 200:
        response_200 = SessionResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SessionResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: SessionRequest,
) -> Response[ErrorResponse | SessionResponse]:
    """Web Start Session

     Start a session, or come back to one: either way, a new access token.

    Args:
        public_key (str):
        body (SessionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SessionResponse]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: SessionRequest,
) -> ErrorResponse | SessionResponse | None:
    """Web Start Session

     Start a session, or come back to one: either way, a new access token.

    Args:
        public_key (str):
        body (SessionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SessionResponse
    """

    return sync_detailed(
        public_key=public_key,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: SessionRequest,
) -> Response[ErrorResponse | SessionResponse]:
    """Web Start Session

     Start a session, or come back to one: either way, a new access token.

    Args:
        public_key (str):
        body (SessionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SessionResponse]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: SessionRequest,
) -> ErrorResponse | SessionResponse | None:
    """Web Start Session

     Start a session, or come back to one: either way, a new access token.

    Args:
        public_key (str):
        body (SessionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SessionResponse
    """

    return (
        await asyncio_detailed(
            public_key=public_key,
            client=client,
            body=body,
        )
    ).parsed
