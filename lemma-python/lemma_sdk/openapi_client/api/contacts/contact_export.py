from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.contact_export_response import ContactExportResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    contact_id: UUID,
    *,
    cursor: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_cursor: None | str | Unset
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/contacts/{contact_id}/export".format(
            pod_id=quote(str(pod_id), safe=""),
            contact_id=quote(str(contact_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ContactExportResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ContactExportResponse.from_dict(response.json())

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
) -> Response[ContactExportResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    cursor: None | str | Unset = UNSET,
) -> Response[ContactExportResponse | ErrorResponse]:
    """Export Contact

     A contact's handles, what was said with them, and the rows that are theirs.

    Takes a pod admin, as forgetting does: both answer the person the data is
    about, not the member reading it.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContactExportResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        contact_id=contact_id,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    cursor: None | str | Unset = UNSET,
) -> ContactExportResponse | ErrorResponse | None:
    """Export Contact

     A contact's handles, what was said with them, and the rows that are theirs.

    Takes a pod admin, as forgetting does: both answer the person the data is
    about, not the member reading it.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContactExportResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        contact_id=contact_id,
        client=client,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    cursor: None | str | Unset = UNSET,
) -> Response[ContactExportResponse | ErrorResponse]:
    """Export Contact

     A contact's handles, what was said with them, and the rows that are theirs.

    Takes a pod admin, as forgetting does: both answer the person the data is
    about, not the member reading it.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContactExportResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        contact_id=contact_id,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    contact_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    cursor: None | str | Unset = UNSET,
) -> ContactExportResponse | ErrorResponse | None:
    """Export Contact

     A contact's handles, what was said with them, and the rows that are theirs.

    Takes a pod admin, as forgetting does: both answer the person the data is
    about, not the member reading it.

    Args:
        pod_id (UUID):
        contact_id (UUID):
        cursor (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContactExportResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            contact_id=contact_id,
            client=client,
            cursor=cursor,
        )
    ).parsed
