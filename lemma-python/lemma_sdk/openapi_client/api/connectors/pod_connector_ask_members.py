from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.ask_members_request import AskMembersRequest
from ...models.ask_members_response import AskMembersResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    slug: str,
    *,
    body: AskMembersRequest | None | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/connectors/{slug}/ask-members".format(
            pod_id=quote(str(pod_id), safe=""),
            slug=quote(str(slug), safe=""),
        ),
    }

    if isinstance(body, AskMembersRequest):
        _kwargs["json"] = body.to_dict()
    else:
        _kwargs["json"] = body

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AskMembersResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AskMembersResponse.from_dict(response.json())

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
) -> Response[AskMembersResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: AskMembersRequest | None | Unset = UNSET,
) -> Response[AskMembersResponse | ErrorResponse]:
    r"""Ask Members To Connect

     Post one line, \"This pod uses <connector> — connect yours: <connect_url>\", in the own channel of
    each member a PER_MEMBER connector is waiting on (as `coverage` counts them). A member is asked
    about one connector at most once a day; those asked already are returned as `asked_recently`, and
    those whose line could not be posted, or whose last ask could not be checked, as `not_asked`.

    Args:
        pod_id (UUID):
        slug (str):
        body (AskMembersRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AskMembersResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: AskMembersRequest | None | Unset = UNSET,
) -> AskMembersResponse | ErrorResponse | None:
    r"""Ask Members To Connect

     Post one line, \"This pod uses <connector> — connect yours: <connect_url>\", in the own channel of
    each member a PER_MEMBER connector is waiting on (as `coverage` counts them). A member is asked
    about one connector at most once a day; those asked already are returned as `asked_recently`, and
    those whose line could not be posted, or whose last ask could not be checked, as `not_asked`.

    Args:
        pod_id (UUID):
        slug (str):
        body (AskMembersRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AskMembersResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        slug=slug,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: AskMembersRequest | None | Unset = UNSET,
) -> Response[AskMembersResponse | ErrorResponse]:
    r"""Ask Members To Connect

     Post one line, \"This pod uses <connector> — connect yours: <connect_url>\", in the own channel of
    each member a PER_MEMBER connector is waiting on (as `coverage` counts them). A member is asked
    about one connector at most once a day; those asked already are returned as `asked_recently`, and
    those whose line could not be posted, or whose last ask could not be checked, as `not_asked`.

    Args:
        pod_id (UUID):
        slug (str):
        body (AskMembersRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AskMembersResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    body: AskMembersRequest | None | Unset = UNSET,
) -> AskMembersResponse | ErrorResponse | None:
    r"""Ask Members To Connect

     Post one line, \"This pod uses <connector> — connect yours: <connect_url>\", in the own channel of
    each member a PER_MEMBER connector is waiting on (as `coverage` counts them). A member is asked
    about one connector at most once a day; those asked already are returned as `asked_recently`, and
    those whose line could not be posted, or whose last ask could not be checked, as `not_asked`.

    Args:
        pod_id (UUID):
        slug (str):
        body (AskMembersRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AskMembersResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            slug=slug,
            client=client,
            body=body,
        )
    ).parsed
