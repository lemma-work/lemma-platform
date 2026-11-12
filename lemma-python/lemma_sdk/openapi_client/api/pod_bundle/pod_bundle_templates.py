from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.outcome_template_card import OutcomeTemplateCard
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/bundle/templates",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> list[OutcomeTemplateCard] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = OutcomeTemplateCard.from_dict(response_200_item_data)

            response_200.append(response_200_item)

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[list[OutcomeTemplateCard]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[list[OutcomeTemplateCard]]:
    """Templates to start a Lemma from

     The templates that ship with this server, one card each: what a Lemma made from it owns and how that
    is counted, its face, where its work happens, what it brings and the first lines of its plan. Start
    one with POST /pods/start.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[list[OutcomeTemplateCard]]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> list[OutcomeTemplateCard] | None:
    """Templates to start a Lemma from

     The templates that ship with this server, one card each: what a Lemma made from it owns and how that
    is counted, its face, where its work happens, what it brings and the first lines of its plan. Start
    one with POST /pods/start.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        list[OutcomeTemplateCard]
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[list[OutcomeTemplateCard]]:
    """Templates to start a Lemma from

     The templates that ship with this server, one card each: what a Lemma made from it owns and how that
    is counted, its face, where its work happens, what it brings and the first lines of its plan. Start
    one with POST /pods/start.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[list[OutcomeTemplateCard]]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> list[OutcomeTemplateCard] | None:
    """Templates to start a Lemma from

     The templates that ship with this server, one card each: what a Lemma made from it owns and how that
    is counted, its face, where its work happens, what it brings and the first lines of its plan. Start
    one with POST /pods/start.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        list[OutcomeTemplateCard]
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
