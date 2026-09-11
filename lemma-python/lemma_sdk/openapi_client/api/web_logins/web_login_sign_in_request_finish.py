from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.finish_sign_in_request import FinishSignInRequest
from ...models.sign_in_request_response import SignInRequestResponse
from ...types import Response


def _get_kwargs(
    request_id: UUID,
    *,
    body: FinishSignInRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/web-logins/sign-in-requests/{request_id}:finish".format(
            request_id=quote(str(request_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SignInRequestResponse | None:
    if response.status_code == 200:
        response_200 = SignInRequestResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SignInRequestResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FinishSignInRequest,
) -> Response[ErrorResponse | SignInRequestResponse]:
    r"""Say you have signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run — so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    Args:
        request_id (UUID):
        body (FinishSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SignInRequestResponse]
    """

    kwargs = _get_kwargs(
        request_id=request_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FinishSignInRequest,
) -> ErrorResponse | SignInRequestResponse | None:
    r"""Say you have signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run — so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    Args:
        request_id (UUID):
        body (FinishSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SignInRequestResponse
    """

    return sync_detailed(
        request_id=request_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FinishSignInRequest,
) -> Response[ErrorResponse | SignInRequestResponse]:
    r"""Say you have signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run — so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    Args:
        request_id (UUID):
        body (FinishSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SignInRequestResponse]
    """

    kwargs = _get_kwargs(
        request_id=request_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    request_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: FinishSignInRequest,
) -> ErrorResponse | SignInRequestResponse | None:
    r"""Say you have signed in

     Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run — so that \"it did not work\" is something they can
    be told at the moment they can still fix it.

    Args:
        request_id (UUID):
        body (FinishSignInRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SignInRequestResponse
    """

    return (
        await asyncio_detailed(
            request_id=request_id,
            client=client,
            body=body,
        )
    ).parsed
