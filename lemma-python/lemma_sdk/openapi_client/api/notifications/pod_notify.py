from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.notify_member_request import NotifyMemberRequest
from ...models.notify_member_response import NotifyMemberResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: NotifyMemberRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/notify".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | NotifyMemberResponse | None:
    if response.status_code == 200:
        response_200 = NotifyMemberResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | NotifyMemberResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotifyMemberRequest,
) -> Response[ErrorResponse | NotifyMemberResponse]:
    """Notify Pod Member

     Tell a pod member something, letting Lemma choose where to reach them.

    Unlike ``surfaces/{name}/send``, which targets one named surface and needs a
    thread the person already started, this picks whichever channel they last
    used and always leaves the message in their Lemma inbox — so it cannot
    silently reach nobody.

    Gated on ``conversation.write`` rather than ``agent.update``: sending
    somebody a message is not an act of editing agents, and requiring an editor
    permission is what kept functions and apps from using this at all.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Reach one pod member, letting Lemma choose where.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotifyMemberResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotifyMemberRequest,
) -> ErrorResponse | NotifyMemberResponse | None:
    """Notify Pod Member

     Tell a pod member something, letting Lemma choose where to reach them.

    Unlike ``surfaces/{name}/send``, which targets one named surface and needs a
    thread the person already started, this picks whichever channel they last
    used and always leaves the message in their Lemma inbox — so it cannot
    silently reach nobody.

    Gated on ``conversation.write`` rather than ``agent.update``: sending
    somebody a message is not an act of editing agents, and requiring an editor
    permission is what kept functions and apps from using this at all.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Reach one pod member, letting Lemma choose where.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotifyMemberResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotifyMemberRequest,
) -> Response[ErrorResponse | NotifyMemberResponse]:
    """Notify Pod Member

     Tell a pod member something, letting Lemma choose where to reach them.

    Unlike ``surfaces/{name}/send``, which targets one named surface and needs a
    thread the person already started, this picks whichever channel they last
    used and always leaves the message in their Lemma inbox — so it cannot
    silently reach nobody.

    Gated on ``conversation.write`` rather than ``agent.update``: sending
    somebody a message is not an act of editing agents, and requiring an editor
    permission is what kept functions and apps from using this at all.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Reach one pod member, letting Lemma choose where.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotifyMemberResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotifyMemberRequest,
) -> ErrorResponse | NotifyMemberResponse | None:
    """Notify Pod Member

     Tell a pod member something, letting Lemma choose where to reach them.

    Unlike ``surfaces/{name}/send``, which targets one named surface and needs a
    thread the person already started, this picks whichever channel they last
    used and always leaves the message in their Lemma inbox — so it cannot
    silently reach nobody.

    Gated on ``conversation.write`` rather than ``agent.update``: sending
    somebody a message is not an act of editing agents, and requiring an editor
    permission is what kept functions and apps from using this at all.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Reach one pod member, letting Lemma choose where.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotifyMemberResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed
