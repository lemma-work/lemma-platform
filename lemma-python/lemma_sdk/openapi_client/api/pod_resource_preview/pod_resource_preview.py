from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.resource_preview_response import ResourcePreviewResponse
from ...models.resource_type import ResourceType
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    resource_type: ResourceType,
    *,
    name: None | str | Unset = UNSET,
    id: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_name: None | str | Unset
    if isinstance(name, Unset):
        json_name = UNSET
    else:
        json_name = name
    params["name"] = json_name

    json_id: None | str | Unset
    if isinstance(id, Unset):
        json_id = UNSET
    elif isinstance(id, UUID):
        json_id = str(id)
    else:
        json_id = id
    params["id"] = json_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/resources/{resource_type}/preview".format(
            pod_id=quote(str(pod_id), safe=""),
            resource_type=quote(str(resource_type), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ResourcePreviewResponse | None:
    if response.status_code == 200:
        response_200 = ResourcePreviewResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ResourcePreviewResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    resource_type: ResourceType,
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    id: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | ResourcePreviewResponse]:
    r"""Preview a Shared Resource

     Describe a shared resource, addressed by id or by name.

    Both, because the two live in different worlds: agents, apps and tables are
    linked by name, while a document's \"name\" is its stored path — which a
    recipient does not have, since the link they were sent carries an id
    precisely so it does not depend on a path.

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        name (None | str | Unset):
        id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourcePreviewResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        resource_type=resource_type,
        name=name,
        id=id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    resource_type: ResourceType,
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    id: None | Unset | UUID = UNSET,
) -> ErrorResponse | ResourcePreviewResponse | None:
    r"""Preview a Shared Resource

     Describe a shared resource, addressed by id or by name.

    Both, because the two live in different worlds: agents, apps and tables are
    linked by name, while a document's \"name\" is its stored path — which a
    recipient does not have, since the link they were sent carries an id
    precisely so it does not depend on a path.

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        name (None | str | Unset):
        id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourcePreviewResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        resource_type=resource_type,
        client=client,
        name=name,
        id=id,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    resource_type: ResourceType,
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    id: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | ResourcePreviewResponse]:
    r"""Preview a Shared Resource

     Describe a shared resource, addressed by id or by name.

    Both, because the two live in different worlds: agents, apps and tables are
    linked by name, while a document's \"name\" is its stored path — which a
    recipient does not have, since the link they were sent carries an id
    precisely so it does not depend on a path.

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        name (None | str | Unset):
        id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourcePreviewResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        resource_type=resource_type,
        name=name,
        id=id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    resource_type: ResourceType,
    *,
    client: AuthenticatedClient | Client,
    name: None | str | Unset = UNSET,
    id: None | Unset | UUID = UNSET,
) -> ErrorResponse | ResourcePreviewResponse | None:
    r"""Preview a Shared Resource

     Describe a shared resource, addressed by id or by name.

    Both, because the two live in different worlds: agents, apps and tables are
    linked by name, while a document's \"name\" is its stored path — which a
    recipient does not have, since the link they were sent carries an id
    precisely so it does not depend on a path.

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        name (None | str | Unset):
        id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourcePreviewResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            resource_type=resource_type,
            client=client,
            name=name,
            id=id,
        )
    ).parsed
