from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.slack_install_availability_response import (
    SlackInstallAvailabilityResponse,
)
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/surface-setup/slack/install",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> SlackInstallAvailabilityResponse | None:
    if response.status_code == 200:
        response_200 = SlackInstallAvailabilityResponse.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[SlackInstallAvailabilityResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[SlackInstallAvailabilityResponse]:
    r"""Slack Install Availability

     Whether Lemma's own Slack app is set up on this server, so \"Add to
    Slack\" can work -- checked before a person with no organization is given
    one for it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[SlackInstallAvailabilityResponse]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> SlackInstallAvailabilityResponse | None:
    r"""Slack Install Availability

     Whether Lemma's own Slack app is set up on this server, so \"Add to
    Slack\" can work -- checked before a person with no organization is given
    one for it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        SlackInstallAvailabilityResponse
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[SlackInstallAvailabilityResponse]:
    r"""Slack Install Availability

     Whether Lemma's own Slack app is set up on this server, so \"Add to
    Slack\" can work -- checked before a person with no organization is given
    one for it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[SlackInstallAvailabilityResponse]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> SlackInstallAvailabilityResponse | None:
    r"""Slack Install Availability

     Whether Lemma's own Slack app is set up on this server, so \"Add to
    Slack\" can work -- checked before a person with no organization is given
    one for it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        SlackInstallAvailabilityResponse
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
