from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.browser_command_request import BrowserCommandRequest
from ...models.browser_command_response import BrowserCommandResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    ref: str,
    *,
    body: BrowserCommandRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/machines/{ref}/browser:command".format(
            ref=quote(str(ref), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BrowserCommandResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = BrowserCommandResponse.from_dict(response.json())

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
) -> Response[BrowserCommandResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: BrowserCommandRequest,
) -> Response[BrowserCommandResponse | ErrorResponse]:
    """Steer a machine's browser from the screen's toolbar

     One toolbar command; the open tabs as they are afterwards.

    409 with a sentence when the browser cannot do it right now -- it is not
    running, the tab has closed -- because the toolbar shows it, and 422 for
    an address that is not a web address.

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (BrowserCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BrowserCommandResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: BrowserCommandRequest,
) -> BrowserCommandResponse | ErrorResponse | None:
    """Steer a machine's browser from the screen's toolbar

     One toolbar command; the open tabs as they are afterwards.

    409 with a sentence when the browser cannot do it right now -- it is not
    running, the tab has closed -- because the toolbar shows it, and 422 for
    an address that is not a web address.

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (BrowserCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BrowserCommandResponse | ErrorResponse
    """

    return sync_detailed(
        ref=ref,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: BrowserCommandRequest,
) -> Response[BrowserCommandResponse | ErrorResponse]:
    """Steer a machine's browser from the screen's toolbar

     One toolbar command; the open tabs as they are afterwards.

    409 with a sentence when the browser cannot do it right now -- it is not
    running, the tab has closed -- because the toolbar shows it, and 422 for
    an address that is not a web address.

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (BrowserCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BrowserCommandResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: BrowserCommandRequest,
) -> BrowserCommandResponse | ErrorResponse | None:
    """Steer a machine's browser from the screen's toolbar

     One toolbar command; the open tabs as they are afterwards.

    409 with a sentence when the browser cannot do it right now -- it is not
    running, the tab has closed -- because the toolbar shows it, and 422 for
    an address that is not a web address.

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (BrowserCommandRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BrowserCommandResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            ref=ref,
            client=client,
            body=body,
        )
    ).parsed
