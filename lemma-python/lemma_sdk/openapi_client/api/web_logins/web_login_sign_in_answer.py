from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.answer_sign_in_request import AnswerSignInRequest
from ...models.error_response import ErrorResponse
from ...models.sign_in_outcome_response import SignInOutcomeResponse
from ...types import Response


def _get_kwargs(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    body: AnswerSignInRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/web-logins/sign-ins/{conversation_id}/{tool_call_id}:answer".format(
            conversation_id=quote(str(conversation_id), safe=""),
            tool_call_id=quote(str(tool_call_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SignInOutcomeResponse | None:
    if response.status_code == 200:
        response_200 = SignInOutcomeResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SignInOutcomeResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerSignInRequest,
) -> Response[ErrorResponse | SignInOutcomeResponse]:
    r"""Say whether you signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run -- so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    One route for both answers because it is one answer. Two routes meant two
    status writes with two different guards, and the weaker one let a stale tab
    overwrite a decision the agent had already been given.

    Args:
        conversation_id (UUID):
        tool_call_id (str):
        body (AnswerSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SignInOutcomeResponse]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerSignInRequest,
) -> ErrorResponse | SignInOutcomeResponse | None:
    r"""Say whether you signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run -- so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    One route for both answers because it is one answer. Two routes meant two
    status writes with two different guards, and the weaker one let a stale tab
    overwrite a decision the agent had already been given.

    Args:
        conversation_id (UUID):
        tool_call_id (str):
        body (AnswerSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SignInOutcomeResponse
    """

    return sync_detailed(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerSignInRequest,
) -> Response[ErrorResponse | SignInOutcomeResponse]:
    r"""Say whether you signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run -- so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    One route for both answers because it is one answer. Two routes meant two
    status writes with two different guards, and the weaker one let a stale tab
    overwrite a decision the agent had already been given.

    Args:
        conversation_id (UUID):
        tool_call_id (str):
        body (AnswerSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SignInOutcomeResponse]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerSignInRequest,
) -> ErrorResponse | SignInOutcomeResponse | None:
    r"""Say whether you signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run -- so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    One route for both answers because it is one answer. Two routes meant two
    status writes with two different guards, and the weaker one let a stale tab
    overwrite a decision the agent had already been given.

    Args:
        conversation_id (UUID):
        tool_call_id (str):
        body (AnswerSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SignInOutcomeResponse
    """

    return (
        await asyncio_detailed(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            client=client,
            body=body,
        )
    ).parsed
