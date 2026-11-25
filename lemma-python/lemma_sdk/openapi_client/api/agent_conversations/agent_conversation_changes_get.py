from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.conversation_changes_response import ConversationChangesResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    patch: bool | Unset = False,
    max_patch_bytes: int | Unset = 262144,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["patch"] = patch

    params["max_patch_bytes"] = max_patch_bytes

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/conversations/{conversation_id}/changes".format(
            pod_id=quote(str(pod_id), safe=""),
            conversation_id=quote(str(conversation_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConversationChangesResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ConversationChangesResponse.from_dict(response.json())

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
) -> Response[ConversationChangesResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    patch: bool | Unset = False,
    max_patch_bytes: int | Unset = 262144,
) -> Response[ConversationChangesResponse | ErrorResponse]:
    """Get Conversation Changes

     What a conversation opened in a repository has changed in its checkout, live: every file that
    differs from where its branch left the start point (`origin/<ref>`, else `origin/HEAD`) -- commits
    not yet pushed, staged and unstaged edits -- and its untracked files, with line counts, and the
    patch with `patch=true`. Read-only: nothing is fetched and the repository is not written. A machine
    that is not running is not started (`machine_unavailable`); a conversation whose last run worked on
    its person's Mac answers `elsewhere`. A read that runs past its ceiling is a 504,
    `CONVERSATION_CHANGES_TIMED_OUT`. Only the conversation's owner may read it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        patch (bool | Unset): Include the unified diff. Default: False.
        max_patch_bytes (int | Unset): Cut the patch at this many bytes; `truncated` says so.
            Default: 262144.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationChangesResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        patch=patch,
        max_patch_bytes=max_patch_bytes,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    patch: bool | Unset = False,
    max_patch_bytes: int | Unset = 262144,
) -> ConversationChangesResponse | ErrorResponse | None:
    """Get Conversation Changes

     What a conversation opened in a repository has changed in its checkout, live: every file that
    differs from where its branch left the start point (`origin/<ref>`, else `origin/HEAD`) -- commits
    not yet pushed, staged and unstaged edits -- and its untracked files, with line counts, and the
    patch with `patch=true`. Read-only: nothing is fetched and the repository is not written. A machine
    that is not running is not started (`machine_unavailable`); a conversation whose last run worked on
    its person's Mac answers `elsewhere`. A read that runs past its ceiling is a 504,
    `CONVERSATION_CHANGES_TIMED_OUT`. Only the conversation's owner may read it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        patch (bool | Unset): Include the unified diff. Default: False.
        max_patch_bytes (int | Unset): Cut the patch at this many bytes; `truncated` says so.
            Default: 262144.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationChangesResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        conversation_id=conversation_id,
        client=client,
        patch=patch,
        max_patch_bytes=max_patch_bytes,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    patch: bool | Unset = False,
    max_patch_bytes: int | Unset = 262144,
) -> Response[ConversationChangesResponse | ErrorResponse]:
    """Get Conversation Changes

     What a conversation opened in a repository has changed in its checkout, live: every file that
    differs from where its branch left the start point (`origin/<ref>`, else `origin/HEAD`) -- commits
    not yet pushed, staged and unstaged edits -- and its untracked files, with line counts, and the
    patch with `patch=true`. Read-only: nothing is fetched and the repository is not written. A machine
    that is not running is not started (`machine_unavailable`); a conversation whose last run worked on
    its person's Mac answers `elsewhere`. A read that runs past its ceiling is a 504,
    `CONVERSATION_CHANGES_TIMED_OUT`. Only the conversation's owner may read it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        patch (bool | Unset): Include the unified diff. Default: False.
        max_patch_bytes (int | Unset): Cut the patch at this many bytes; `truncated` says so.
            Default: 262144.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationChangesResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        patch=patch,
        max_patch_bytes=max_patch_bytes,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    patch: bool | Unset = False,
    max_patch_bytes: int | Unset = 262144,
) -> ConversationChangesResponse | ErrorResponse | None:
    """Get Conversation Changes

     What a conversation opened in a repository has changed in its checkout, live: every file that
    differs from where its branch left the start point (`origin/<ref>`, else `origin/HEAD`) -- commits
    not yet pushed, staged and unstaged edits -- and its untracked files, with line counts, and the
    patch with `patch=true`. Read-only: nothing is fetched and the repository is not written. A machine
    that is not running is not started (`machine_unavailable`); a conversation whose last run worked on
    its person's Mac answers `elsewhere`. A read that runs past its ceiling is a 504,
    `CONVERSATION_CHANGES_TIMED_OUT`. Only the conversation's owner may read it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        patch (bool | Unset): Include the unified diff. Default: False.
        max_patch_bytes (int | Unset): Cut the patch at this many bytes; `truncated` says so.
            Default: 262144.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationChangesResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            conversation_id=conversation_id,
            client=client,
            patch=patch,
            max_patch_bytes=max_patch_bytes,
        )
    ).parsed
