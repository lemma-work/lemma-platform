from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.answer_body import AnswerBody
from ...models.decision_response import DecisionResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    decision_id: UUID,
    *,
    body: AnswerBody,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/decisions/{decision_id}/answer".format(
            pod_id=quote(str(pod_id), safe=""),
            decision_id=quote(str(decision_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DecisionResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DecisionResponse.from_dict(response.json())

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
) -> Response[DecisionResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    decision_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerBody,
) -> Response[DecisionResponse | ErrorResponse]:
    """Answer a decision

     Answer questions a decision left open, or correct a machine's answer. A person's answer becomes an
    example the decider learns from.

    Args:
        pod_id (UUID):
        decision_id (UUID):
        body (AnswerBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DecisionResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decision_id=decision_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    decision_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerBody,
) -> DecisionResponse | ErrorResponse | None:
    """Answer a decision

     Answer questions a decision left open, or correct a machine's answer. A person's answer becomes an
    example the decider learns from.

    Args:
        pod_id (UUID):
        decision_id (UUID):
        body (AnswerBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DecisionResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        decision_id=decision_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    decision_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerBody,
) -> Response[DecisionResponse | ErrorResponse]:
    """Answer a decision

     Answer questions a decision left open, or correct a machine's answer. A person's answer becomes an
    example the decider learns from.

    Args:
        pod_id (UUID):
        decision_id (UUID):
        body (AnswerBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DecisionResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decision_id=decision_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    decision_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AnswerBody,
) -> DecisionResponse | ErrorResponse | None:
    """Answer a decision

     Answer questions a decision left open, or correct a machine's answer. A person's answer becomes an
    example the decider learns from.

    Args:
        pod_id (UUID):
        decision_id (UUID):
        body (AnswerBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DecisionResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            decision_id=decision_id,
            client=client,
            body=body,
        )
    ).parsed
