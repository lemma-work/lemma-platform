from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_list_response import AgentListResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    include: list[str] | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params["limit"] = limit

    json_include: list[str] | Unset = UNSET
    if not isinstance(include, Unset):
        json_include = include

    params["include"] = json_include

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/agents".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentListResponse.from_dict(response.json())

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
) -> Response[AgentListResponse | ErrorResponse]:
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
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    include: list[str] | Unset = UNSET,
) -> Response[AgentListResponse | ErrorResponse]:
    """List Agents

     List pod-owned agent definitions visible to the current user.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 100.
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each agent's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-agent permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        page_token=page_token,
        limit=limit,
        include=include,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    include: list[str] | Unset = UNSET,
) -> AgentListResponse | ErrorResponse | None:
    """List Agents

     List pod-owned agent definitions visible to the current user.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 100.
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each agent's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-agent permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentListResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        page_token=page_token,
        limit=limit,
        include=include,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    include: list[str] | Unset = UNSET,
) -> Response[AgentListResponse | ErrorResponse]:
    """List Agents

     List pod-owned agent definitions visible to the current user.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 100.
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each agent's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-agent permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        page_token=page_token,
        limit=limit,
        include=include,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    include: list[str] | Unset = UNSET,
) -> AgentListResponse | ErrorResponse | None:
    """List Agents

     List pod-owned agent definitions visible to the current user.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 100.
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each agent's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-agent permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            page_token=page_token,
            limit=limit,
            include=include,
        )
    ).parsed
