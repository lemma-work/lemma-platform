from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.start_lemma_request import StartLemmaRequest
from ...models.start_lemma_response import StartLemmaResponse
from ...types import Response


def _get_kwargs(
    *,
    body: StartLemmaRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/start",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | StartLemmaResponse | None:
    if response.status_code == 201:
        response_201 = StartLemmaResponse.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | StartLemmaResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: StartLemmaRequest,
) -> Response[ErrorResponse | StartLemmaResponse]:
    """Start a Lemma

     Make a pod that owns something, in one transaction: the pod, what it owns, a first /plan.md, and the
    chat where its agent sets itself up, opened with the caller's first message. From a template
    (`template`, with `due` for a finish line) it also brings the template's tables, skill and
    schedules, applied by a worker, and its agent starts once they are in; from the caller's own words
    (`outcome` and `name`) its agent starts at once. A failure leaves nothing behind. 404 for a template
    that is not here; 403 outside the organization; 409 when its name collides with one made at the same
    moment.

    Args:
        body (StartLemmaRequest): Body for starting a Lemma: from a template, or from its own
            words.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | StartLemmaResponse]
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
    body: StartLemmaRequest,
) -> ErrorResponse | StartLemmaResponse | None:
    """Start a Lemma

     Make a pod that owns something, in one transaction: the pod, what it owns, a first /plan.md, and the
    chat where its agent sets itself up, opened with the caller's first message. From a template
    (`template`, with `due` for a finish line) it also brings the template's tables, skill and
    schedules, applied by a worker, and its agent starts once they are in; from the caller's own words
    (`outcome` and `name`) its agent starts at once. A failure leaves nothing behind. 404 for a template
    that is not here; 403 outside the organization; 409 when its name collides with one made at the same
    moment.

    Args:
        body (StartLemmaRequest): Body for starting a Lemma: from a template, or from its own
            words.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | StartLemmaResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: StartLemmaRequest,
) -> Response[ErrorResponse | StartLemmaResponse]:
    """Start a Lemma

     Make a pod that owns something, in one transaction: the pod, what it owns, a first /plan.md, and the
    chat where its agent sets itself up, opened with the caller's first message. From a template
    (`template`, with `due` for a finish line) it also brings the template's tables, skill and
    schedules, applied by a worker, and its agent starts once they are in; from the caller's own words
    (`outcome` and `name`) its agent starts at once. A failure leaves nothing behind. 404 for a template
    that is not here; 403 outside the organization; 409 when its name collides with one made at the same
    moment.

    Args:
        body (StartLemmaRequest): Body for starting a Lemma: from a template, or from its own
            words.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | StartLemmaResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: StartLemmaRequest,
) -> ErrorResponse | StartLemmaResponse | None:
    """Start a Lemma

     Make a pod that owns something, in one transaction: the pod, what it owns, a first /plan.md, and the
    chat where its agent sets itself up, opened with the caller's first message. From a template
    (`template`, with `due` for a finish line) it also brings the template's tables, skill and
    schedules, applied by a worker, and its agent starts once they are in; from the caller's own words
    (`outcome` and `name`) its agent starts at once. A failure leaves nothing behind. 404 for a template
    that is not here; 403 outside the organization; 409 when its name collides with one made at the same
    moment.

    Args:
        body (StartLemmaRequest): Body for starting a Lemma: from a template, or from its own
            words.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | StartLemmaResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
