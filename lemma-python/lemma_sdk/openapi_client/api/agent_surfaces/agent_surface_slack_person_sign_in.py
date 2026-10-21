from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.slack_sign_in_request import SlackSignInRequest
from ...models.slack_sign_in_response import SlackSignInResponse
from ...types import Response


def _get_kwargs(
    *,
    body: SlackSignInRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/surface-setup/slack/people/sign-in",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SlackSignInResponse | None:
    if response.status_code == 200:
        response_200 = SlackSignInResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SlackSignInResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SlackSignInRequest,
) -> Response[ErrorResponse | SlackSignInResponse]:
    """Start Slack Sign In

     Start Sign in with Slack for the signed-in person, to prove they are the
    Slack user their App Home's link was for.

    Connects nothing. The callback connects only when it sees this same Lemma
    session and Slack names this same Slack user and workspace.

    Args:
        body (SlackSignInRequest): Whom the App Home link was for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackSignInResponse]
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
    body: SlackSignInRequest,
) -> ErrorResponse | SlackSignInResponse | None:
    """Start Slack Sign In

     Start Sign in with Slack for the signed-in person, to prove they are the
    Slack user their App Home's link was for.

    Connects nothing. The callback connects only when it sees this same Lemma
    session and Slack names this same Slack user and workspace.

    Args:
        body (SlackSignInRequest): Whom the App Home link was for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackSignInResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SlackSignInRequest,
) -> Response[ErrorResponse | SlackSignInResponse]:
    """Start Slack Sign In

     Start Sign in with Slack for the signed-in person, to prove they are the
    Slack user their App Home's link was for.

    Connects nothing. The callback connects only when it sees this same Lemma
    session and Slack names this same Slack user and workspace.

    Args:
        body (SlackSignInRequest): Whom the App Home link was for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackSignInResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: SlackSignInRequest,
) -> ErrorResponse | SlackSignInResponse | None:
    """Start Slack Sign In

     Start Sign in with Slack for the signed-in person, to prove they are the
    Slack user their App Home's link was for.

    Connects nothing. The callback connects only when it sees this same Lemma
    session and Slack names this same Slack user and workspace.

    Args:
        body (SlackSignInRequest): Whom the App Home link was for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackSignInResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
