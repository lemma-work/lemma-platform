from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.account_deletion_request import AccountDeletionRequest
from ...models.account_deletion_response import AccountDeletionResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    *,
    body: AccountDeletionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/users/me",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AccountDeletionResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AccountDeletionResponse.from_dict(response.json())

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
) -> Response[AccountDeletionResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: AccountDeletionRequest,
) -> Response[AccountDeletionResponse | ErrorResponse]:
    r"""Delete Current User

     Delete the signed-in person's account, for good. Their profile is erased, they leave every
    organization, and organizations nobody else is in are deleted with their pods. Work shared with
    others stays, shown as by \"Deleted user\". Every session ends, the caller's included: its cookies
    or tokens are cleared on this response. Refused with 409 ACCOUNT_DELETION_SOLE_OWNER while the
    person is the only owner of an organization others are still in, and with 409 and the billing
    provider's code while something they pay for stands in the way.

    Args:
        body (AccountDeletionRequest): Proof that the person means this account, typed back by
            them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountDeletionResponse | ErrorResponse]
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
    body: AccountDeletionRequest,
) -> AccountDeletionResponse | ErrorResponse | None:
    r"""Delete Current User

     Delete the signed-in person's account, for good. Their profile is erased, they leave every
    organization, and organizations nobody else is in are deleted with their pods. Work shared with
    others stays, shown as by \"Deleted user\". Every session ends, the caller's included: its cookies
    or tokens are cleared on this response. Refused with 409 ACCOUNT_DELETION_SOLE_OWNER while the
    person is the only owner of an organization others are still in, and with 409 and the billing
    provider's code while something they pay for stands in the way.

    Args:
        body (AccountDeletionRequest): Proof that the person means this account, typed back by
            them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountDeletionResponse | ErrorResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: AccountDeletionRequest,
) -> Response[AccountDeletionResponse | ErrorResponse]:
    r"""Delete Current User

     Delete the signed-in person's account, for good. Their profile is erased, they leave every
    organization, and organizations nobody else is in are deleted with their pods. Work shared with
    others stays, shown as by \"Deleted user\". Every session ends, the caller's included: its cookies
    or tokens are cleared on this response. Refused with 409 ACCOUNT_DELETION_SOLE_OWNER while the
    person is the only owner of an organization others are still in, and with 409 and the billing
    provider's code while something they pay for stands in the way.

    Args:
        body (AccountDeletionRequest): Proof that the person means this account, typed back by
            them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountDeletionResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: AccountDeletionRequest,
) -> AccountDeletionResponse | ErrorResponse | None:
    r"""Delete Current User

     Delete the signed-in person's account, for good. Their profile is erased, they leave every
    organization, and organizations nobody else is in are deleted with their pods. Work shared with
    others stays, shown as by \"Deleted user\". Every session ends, the caller's included: its cookies
    or tokens are cleared on this response. Refused with 409 ACCOUNT_DELETION_SOLE_OWNER while the
    person is the only owner of an organization others are still in, and with 409 and the billing
    provider's code while something they pay for stands in the way.

    Args:
        body (AccountDeletionRequest): Proof that the person means this account, typed back by
            them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountDeletionResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
