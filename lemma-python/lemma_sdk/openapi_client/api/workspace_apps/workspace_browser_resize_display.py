from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.display_size_request import DisplaySizeRequest
from ...models.display_size_response import DisplaySizeResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    *,
    body: DisplaySizeRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/workspace/browser/display-size",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DisplaySizeResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DisplaySizeResponse.from_dict(response.json())

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
) -> Response[DisplaySizeResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
) -> Response[DisplaySizeResponse | ErrorResponse]:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DisplaySizeResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
) -> DisplaySizeResponse | ErrorResponse | None:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DisplaySizeResponse | ErrorResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
) -> Response[DisplaySizeResponse | ErrorResponse]:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DisplaySizeResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
) -> DisplaySizeResponse | ErrorResponse | None:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DisplaySizeResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
