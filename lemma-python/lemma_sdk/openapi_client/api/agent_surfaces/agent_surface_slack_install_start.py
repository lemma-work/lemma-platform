from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.slack_install_response import SlackInstallResponse
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/surface-setup/slack/install",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> SlackInstallResponse | None:
    if response.status_code == 200:
        response_200 = SlackInstallResponse.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[SlackInstallResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[SlackInstallResponse]:
    r"""Start Slack Install Flow

     Add Lemma to a Slack workspace, from \"Add to Slack\" rather than from a pod.

    Gives the signed-in person an organization and a first teammate if they
    have neither, and answers with Slack's consent page for Lemma's own app.
    When the grant comes back, the workspace becomes the organization's and
    Lemma's app says hello there.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[SlackInstallResponse]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> SlackInstallResponse | None:
    r"""Start Slack Install Flow

     Add Lemma to a Slack workspace, from \"Add to Slack\" rather than from a pod.

    Gives the signed-in person an organization and a first teammate if they
    have neither, and answers with Slack's consent page for Lemma's own app.
    When the grant comes back, the workspace becomes the organization's and
    Lemma's app says hello there.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        SlackInstallResponse
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[SlackInstallResponse]:
    r"""Start Slack Install Flow

     Add Lemma to a Slack workspace, from \"Add to Slack\" rather than from a pod.

    Gives the signed-in person an organization and a first teammate if they
    have neither, and answers with Slack's consent page for Lemma's own app.
    When the grant comes back, the workspace becomes the organization's and
    Lemma's app says hello there.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[SlackInstallResponse]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> SlackInstallResponse | None:
    r"""Start Slack Install Flow

     Add Lemma to a Slack workspace, from \"Add to Slack\" rather than from a pod.

    Gives the signed-in person an organization and a first teammate if they
    have neither, and answers with Slack's consent page for Lemma's own app.
    When the grant comes back, the workspace becomes the organization's and
    Lemma's app says hello there.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        SlackInstallResponse
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
