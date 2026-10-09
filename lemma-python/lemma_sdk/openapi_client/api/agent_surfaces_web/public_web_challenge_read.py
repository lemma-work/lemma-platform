from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.public_web_challenge_read_response_public_web_challenge_read import (
    PublicWebChallengeReadResponsePublicWebChallengeRead,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    public_key: str,
    *,
    purpose: str | Unset = "session",
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["purpose"] = purpose

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/public/web/{public_key}/challenge".format(
            public_key=quote(str(public_key), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead | None:
    if response.status_code == 200:
        response_200 = PublicWebChallengeReadResponsePublicWebChallengeRead.from_dict(
            response.json()
        )

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
) -> Response[ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead]:
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
    purpose: str | Unset = "session",
) -> Response[ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead]:
    r"""Web Challenge

     A proof-of-work to solve before starting a session or asking for a code.

    ``{\"enabled\": false}`` when the deployment has bot protection off.

    Args:
        public_key (str):
        purpose (str | Unset):  Default: 'session'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        purpose=purpose,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    purpose: str | Unset = "session",
) -> ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead | None:
    r"""Web Challenge

     A proof-of-work to solve before starting a session or asking for a code.

    ``{\"enabled\": false}`` when the deployment has bot protection off.

    Args:
        public_key (str):
        purpose (str | Unset):  Default: 'session'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead
    """

    return sync_detailed(
        public_key=public_key,
        client=client,
        purpose=purpose,
    ).parsed


async def asyncio_detailed(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    purpose: str | Unset = "session",
) -> Response[ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead]:
    r"""Web Challenge

     A proof-of-work to solve before starting a session or asking for a code.

    ``{\"enabled\": false}`` when the deployment has bot protection off.

    Args:
        public_key (str):
        purpose (str | Unset):  Default: 'session'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        purpose=purpose,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    purpose: str | Unset = "session",
) -> ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead | None:
    r"""Web Challenge

     A proof-of-work to solve before starting a session or asking for a code.

    ``{\"enabled\": false}`` when the deployment has bot protection off.

    Args:
        public_key (str):
        purpose (str | Unset):  Default: 'session'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PublicWebChallengeReadResponsePublicWebChallengeRead
    """

    return (
        await asyncio_detailed(
            public_key=public_key,
            client=client,
            purpose=purpose,
        )
    ).parsed
