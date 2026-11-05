from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.channel_kind import ChannelKind
from ...models.channel_list_response import ChannelListResponse
from ...models.contact_status import ContactStatus
from ...models.error_response import ErrorResponse
from ...models.visibility import Visibility
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    kind: ChannelKind | None | Unset = UNSET,
    visibility: None | Unset | Visibility = UNSET,
    q: None | str | Unset = UNSET,
    archived: bool | Unset = False,
    contact_id: None | Unset | UUID = UNSET,
    assignee: None | str | Unset = UNSET,
    unassigned: bool | Unset = False,
    status: ContactStatus | None | Unset = UNSET,
    needs_person: bool | Unset = False,
    joined: bool | Unset = False,
    source_surface: None | str | Unset = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_kind: None | str | Unset
    if isinstance(kind, Unset):
        json_kind = UNSET
    elif isinstance(kind, ChannelKind):
        json_kind = kind.value
    else:
        json_kind = kind
    params["kind"] = json_kind

    json_visibility: None | str | Unset
    if isinstance(visibility, Unset):
        json_visibility = UNSET
    elif isinstance(visibility, Visibility):
        json_visibility = visibility.value
    else:
        json_visibility = visibility
    params["visibility"] = json_visibility

    json_q: None | str | Unset
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    params["archived"] = archived

    json_contact_id: None | str | Unset
    if isinstance(contact_id, Unset):
        json_contact_id = UNSET
    elif isinstance(contact_id, UUID):
        json_contact_id = str(contact_id)
    else:
        json_contact_id = contact_id
    params["contact_id"] = json_contact_id

    json_assignee: None | str | Unset
    if isinstance(assignee, Unset):
        json_assignee = UNSET
    else:
        json_assignee = assignee
    params["assignee"] = json_assignee

    params["unassigned"] = unassigned

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    elif isinstance(status, ContactStatus):
        json_status = status.value
    else:
        json_status = status
    params["status"] = json_status

    params["needs_person"] = needs_person

    params["joined"] = joined

    json_source_surface: None | str | Unset
    if isinstance(source_surface, Unset):
        json_source_surface = UNSET
    else:
        json_source_surface = source_surface
    params["source_surface"] = json_source_surface

    json_cursor: None | str | Unset
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/channels".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ChannelListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ChannelListResponse.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ChannelListResponse | ErrorResponse]:
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
    kind: ChannelKind | None | Unset = UNSET,
    visibility: None | Unset | Visibility = UNSET,
    q: None | str | Unset = UNSET,
    archived: bool | Unset = False,
    contact_id: None | Unset | UUID = UNSET,
    assignee: None | str | Unset = UNSET,
    unassigned: bool | Unset = False,
    status: ContactStatus | None | Unset = UNSET,
    needs_person: bool | Unset = False,
    joined: bool | Unset = False,
    source_surface: None | str | Unset = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ChannelListResponse | ErrorResponse]:
    """List Channels

     The channels the reader may see, most recently active first, each with
    what is new in it for them and its last line.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        visibility (None | Unset | Visibility): Only `PUBLIC` or only `PRIVATE` channels; a
            member's and a contact's own channel are private.
        q (None | str | Unset): Only channels named by every one of these words, in any order and
            case: by title, or -- a member's or a contact's own channel -- by whose it is.
        archived (bool | Unset):  Default: False.
        contact_id (None | Unset | UUID): Only this contact's channel.
        assignee (None | str | Unset): `me`, or a member's user id: contacts' channels assigned to
            them.
        unassigned (bool | Unset):  Default: False.
        status (ContactStatus | None | Unset):
        needs_person (bool | Unset): Only contacts' channels waiting on a person: open,
            unassigned, and the agent off or asking for one. Default: False.
        joined (bool | Unset): Only channels the reader is in. Default: False.
        source_surface (None | str | Unset): A bot's surface name: only the groups it is in, each
            with its `source_settings`. Takes reading the bot.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        kind=kind,
        visibility=visibility,
        q=q,
        archived=archived,
        contact_id=contact_id,
        assignee=assignee,
        unassigned=unassigned,
        status=status,
        needs_person=needs_person,
        joined=joined,
        source_surface=source_surface,
        cursor=cursor,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    kind: ChannelKind | None | Unset = UNSET,
    visibility: None | Unset | Visibility = UNSET,
    q: None | str | Unset = UNSET,
    archived: bool | Unset = False,
    contact_id: None | Unset | UUID = UNSET,
    assignee: None | str | Unset = UNSET,
    unassigned: bool | Unset = False,
    status: ContactStatus | None | Unset = UNSET,
    needs_person: bool | Unset = False,
    joined: bool | Unset = False,
    source_surface: None | str | Unset = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> ChannelListResponse | ErrorResponse | None:
    """List Channels

     The channels the reader may see, most recently active first, each with
    what is new in it for them and its last line.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        visibility (None | Unset | Visibility): Only `PUBLIC` or only `PRIVATE` channels; a
            member's and a contact's own channel are private.
        q (None | str | Unset): Only channels named by every one of these words, in any order and
            case: by title, or -- a member's or a contact's own channel -- by whose it is.
        archived (bool | Unset):  Default: False.
        contact_id (None | Unset | UUID): Only this contact's channel.
        assignee (None | str | Unset): `me`, or a member's user id: contacts' channels assigned to
            them.
        unassigned (bool | Unset):  Default: False.
        status (ContactStatus | None | Unset):
        needs_person (bool | Unset): Only contacts' channels waiting on a person: open,
            unassigned, and the agent off or asking for one. Default: False.
        joined (bool | Unset): Only channels the reader is in. Default: False.
        source_surface (None | str | Unset): A bot's surface name: only the groups it is in, each
            with its `source_settings`. Takes reading the bot.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelListResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        kind=kind,
        visibility=visibility,
        q=q,
        archived=archived,
        contact_id=contact_id,
        assignee=assignee,
        unassigned=unassigned,
        status=status,
        needs_person=needs_person,
        joined=joined,
        source_surface=source_surface,
        cursor=cursor,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    kind: ChannelKind | None | Unset = UNSET,
    visibility: None | Unset | Visibility = UNSET,
    q: None | str | Unset = UNSET,
    archived: bool | Unset = False,
    contact_id: None | Unset | UUID = UNSET,
    assignee: None | str | Unset = UNSET,
    unassigned: bool | Unset = False,
    status: ContactStatus | None | Unset = UNSET,
    needs_person: bool | Unset = False,
    joined: bool | Unset = False,
    source_surface: None | str | Unset = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ChannelListResponse | ErrorResponse]:
    """List Channels

     The channels the reader may see, most recently active first, each with
    what is new in it for them and its last line.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        visibility (None | Unset | Visibility): Only `PUBLIC` or only `PRIVATE` channels; a
            member's and a contact's own channel are private.
        q (None | str | Unset): Only channels named by every one of these words, in any order and
            case: by title, or -- a member's or a contact's own channel -- by whose it is.
        archived (bool | Unset):  Default: False.
        contact_id (None | Unset | UUID): Only this contact's channel.
        assignee (None | str | Unset): `me`, or a member's user id: contacts' channels assigned to
            them.
        unassigned (bool | Unset):  Default: False.
        status (ContactStatus | None | Unset):
        needs_person (bool | Unset): Only contacts' channels waiting on a person: open,
            unassigned, and the agent off or asking for one. Default: False.
        joined (bool | Unset): Only channels the reader is in. Default: False.
        source_surface (None | str | Unset): A bot's surface name: only the groups it is in, each
            with its `source_settings`. Takes reading the bot.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        kind=kind,
        visibility=visibility,
        q=q,
        archived=archived,
        contact_id=contact_id,
        assignee=assignee,
        unassigned=unassigned,
        status=status,
        needs_person=needs_person,
        joined=joined,
        source_surface=source_surface,
        cursor=cursor,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    kind: ChannelKind | None | Unset = UNSET,
    visibility: None | Unset | Visibility = UNSET,
    q: None | str | Unset = UNSET,
    archived: bool | Unset = False,
    contact_id: None | Unset | UUID = UNSET,
    assignee: None | str | Unset = UNSET,
    unassigned: bool | Unset = False,
    status: ContactStatus | None | Unset = UNSET,
    needs_person: bool | Unset = False,
    joined: bool | Unset = False,
    source_surface: None | str | Unset = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> ChannelListResponse | ErrorResponse | None:
    """List Channels

     The channels the reader may see, most recently active first, each with
    what is new in it for them and its last line.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        visibility (None | Unset | Visibility): Only `PUBLIC` or only `PRIVATE` channels; a
            member's and a contact's own channel are private.
        q (None | str | Unset): Only channels named by every one of these words, in any order and
            case: by title, or -- a member's or a contact's own channel -- by whose it is.
        archived (bool | Unset):  Default: False.
        contact_id (None | Unset | UUID): Only this contact's channel.
        assignee (None | str | Unset): `me`, or a member's user id: contacts' channels assigned to
            them.
        unassigned (bool | Unset):  Default: False.
        status (ContactStatus | None | Unset):
        needs_person (bool | Unset): Only contacts' channels waiting on a person: open,
            unassigned, and the agent off or asking for one. Default: False.
        joined (bool | Unset): Only channels the reader is in. Default: False.
        source_surface (None | str | Unset): A bot's surface name: only the groups it is in, each
            with its `source_settings`. Takes reading the bot.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            kind=kind,
            visibility=visibility,
            q=q,
            archived=archived,
            contact_id=contact_id,
            assignee=assignee,
            unassigned=unassigned,
            status=status,
            needs_person=needs_person,
            joined=joined,
            source_surface=source_surface,
            cursor=cursor,
            limit=limit,
        )
    ).parsed
