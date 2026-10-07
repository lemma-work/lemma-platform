from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.template_list_response import TemplateListResponse
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/bundle/templates",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> TemplateListResponse | None:
    if response.status_code == 200:
        response_200 = TemplateListResponse.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[TemplateListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[TemplateListResponse]:
    """List Role Templates

     The templates that ship with Lemma and carry a role card, in shelf order: the job in one line, what
    arrives, first things to say, standing work on offer, and what the role is judged on, read from the
    template itself. Hire one by importing it with kind=TEMPLATE into a new pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[TemplateListResponse]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> TemplateListResponse | None:
    """List Role Templates

     The templates that ship with Lemma and carry a role card, in shelf order: the job in one line, what
    arrives, first things to say, standing work on offer, and what the role is judged on, read from the
    template itself. Hire one by importing it with kind=TEMPLATE into a new pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        TemplateListResponse
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[TemplateListResponse]:
    """List Role Templates

     The templates that ship with Lemma and carry a role card, in shelf order: the job in one line, what
    arrives, first things to say, standing work on offer, and what the role is judged on, read from the
    template itself. Hire one by importing it with kind=TEMPLATE into a new pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[TemplateListResponse]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> TemplateListResponse | None:
    """List Role Templates

     The templates that ship with Lemma and carry a role card, in shelf order: the job in one line, what
    arrives, first things to say, standing work on offer, and what the role is judged on, read from the
    template itself. Hire one by importing it with kind=TEMPLATE into a new pod.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        TemplateListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
