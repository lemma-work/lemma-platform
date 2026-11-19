from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.user_response import UserResponse
from ...models.work_answer_request import WorkAnswerRequest
from ...types import Response


def _get_kwargs(
    *,
    body: WorkAnswerRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/users/me/work",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | UserResponse | None:
    if response.status_code == 200:
        response_200 = UserResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | UserResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: WorkAnswerRequest,
) -> Response[ErrorResponse | UserResponse]:
    """Say What The Current User Does

     Keep what the current user said they do, replacing any earlier answer, and return the user with it.
    Asked once, at their first start: an answer with no jobs and no `other` records that they skipped,
    so they are not asked again. Leaves every other preference as it was.

    Args:
        body (WorkAnswerRequest): What the person does, in answer to the one time they are asked.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UserResponse]
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
    body: WorkAnswerRequest,
) -> ErrorResponse | UserResponse | None:
    """Say What The Current User Does

     Keep what the current user said they do, replacing any earlier answer, and return the user with it.
    Asked once, at their first start: an answer with no jobs and no `other` records that they skipped,
    so they are not asked again. Leaves every other preference as it was.

    Args:
        body (WorkAnswerRequest): What the person does, in answer to the one time they are asked.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UserResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: WorkAnswerRequest,
) -> Response[ErrorResponse | UserResponse]:
    """Say What The Current User Does

     Keep what the current user said they do, replacing any earlier answer, and return the user with it.
    Asked once, at their first start: an answer with no jobs and no `other` records that they skipped,
    so they are not asked again. Leaves every other preference as it was.

    Args:
        body (WorkAnswerRequest): What the person does, in answer to the one time they are asked.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UserResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: WorkAnswerRequest,
) -> ErrorResponse | UserResponse | None:
    """Say What The Current User Does

     Keep what the current user said they do, replacing any earlier answer, and return the user with it.
    Asked once, at their first start: an answer with no jobs and no `other` records that they skipped,
    so they are not asked again. Leaves every other preference as it was.

    Args:
        body (WorkAnswerRequest): What the person does, in answer to the one time they are asked.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UserResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
