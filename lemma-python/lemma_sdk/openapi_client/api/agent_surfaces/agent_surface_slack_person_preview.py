from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.slack_person_link_preview_response import SlackPersonLinkPreviewResponse
from ...models.slack_person_link_request import SlackPersonLinkRequest
from ...types import Response


def _get_kwargs(
    *,
    body: SlackPersonLinkRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/surface-setup/slack/people/preview",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SlackPersonLinkPreviewResponse | None:
    if response.status_code == 200:
        response_200 = SlackPersonLinkPreviewResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SlackPersonLinkPreviewResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SlackPersonLinkRequest,
) -> Response[ErrorResponse | SlackPersonLinkPreviewResponse]:
    """Preview Slack Person Link

     Say whose Slack account this link would connect to the signed-in person.

    Connects nothing. The page shows it and asks first, so a link somebody
    else sent cannot connect their Slack account to yours unannounced.

    Args:
        body (SlackPersonLinkRequest): The values in the link a Slack user's App Home showed them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackPersonLinkPreviewResponse]
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
    body: SlackPersonLinkRequest,
) -> ErrorResponse | SlackPersonLinkPreviewResponse | None:
    """Preview Slack Person Link

     Say whose Slack account this link would connect to the signed-in person.

    Connects nothing. The page shows it and asks first, so a link somebody
    else sent cannot connect their Slack account to yours unannounced.

    Args:
        body (SlackPersonLinkRequest): The values in the link a Slack user's App Home showed them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackPersonLinkPreviewResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SlackPersonLinkRequest,
) -> Response[ErrorResponse | SlackPersonLinkPreviewResponse]:
    """Preview Slack Person Link

     Say whose Slack account this link would connect to the signed-in person.

    Connects nothing. The page shows it and asks first, so a link somebody
    else sent cannot connect their Slack account to yours unannounced.

    Args:
        body (SlackPersonLinkRequest): The values in the link a Slack user's App Home showed them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackPersonLinkPreviewResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: SlackPersonLinkRequest,
) -> ErrorResponse | SlackPersonLinkPreviewResponse | None:
    """Preview Slack Person Link

     Say whose Slack account this link would connect to the signed-in person.

    Connects nothing. The page shows it and asks first, so a link somebody
    else sent cannot connect their Slack account to yours unannounced.

    Args:
        body (SlackPersonLinkRequest): The values in the link a Slack user's App Home showed them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackPersonLinkPreviewResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
