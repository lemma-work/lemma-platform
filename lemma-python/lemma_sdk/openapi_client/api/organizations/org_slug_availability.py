from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.organization_slug_availability_response import (
    OrganizationSlugAvailabilityResponse,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    slug: str,
    name: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["slug"] = slug

    json_name: None | str | Unset
    if isinstance(name, Unset):
        json_name = UNSET
    else:
        json_name = name
    params["name"] = json_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/slug-availability",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OrganizationSlugAvailabilityResponse | None:
    if response.status_code == 200:
        response_200 = OrganizationSlugAvailabilityResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OrganizationSlugAvailabilityResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    slug: str,
    name: None | str | Unset = UNSET,
) -> Response[ErrorResponse | OrganizationSlugAvailabilityResponse]:
    """Check Organization Slug Availability

     Check whether an organization slug — the handle, and the only unique one of the two — is available.
    `name_available` is deprecated and always true: display names are labels, and two organizations may
    share one.

    Args:
        slug (str):
        name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationSlugAvailabilityResponse]
    """

    kwargs = _get_kwargs(
        slug=slug,
        name=name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    slug: str,
    name: None | str | Unset = UNSET,
) -> ErrorResponse | OrganizationSlugAvailabilityResponse | None:
    """Check Organization Slug Availability

     Check whether an organization slug — the handle, and the only unique one of the two — is available.
    `name_available` is deprecated and always true: display names are labels, and two organizations may
    share one.

    Args:
        slug (str):
        name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationSlugAvailabilityResponse
    """

    return sync_detailed(
        client=client,
        slug=slug,
        name=name,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    slug: str,
    name: None | str | Unset = UNSET,
) -> Response[ErrorResponse | OrganizationSlugAvailabilityResponse]:
    """Check Organization Slug Availability

     Check whether an organization slug — the handle, and the only unique one of the two — is available.
    `name_available` is deprecated and always true: display names are labels, and two organizations may
    share one.

    Args:
        slug (str):
        name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationSlugAvailabilityResponse]
    """

    kwargs = _get_kwargs(
        slug=slug,
        name=name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    slug: str,
    name: None | str | Unset = UNSET,
) -> ErrorResponse | OrganizationSlugAvailabilityResponse | None:
    """Check Organization Slug Availability

     Check whether an organization slug — the handle, and the only unique one of the two — is available.
    `name_available` is deprecated and always true: display names are labels, and two organizations may
    share one.

    Args:
        slug (str):
        name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationSlugAvailabilityResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            slug=slug,
            name=name,
        )
    ).parsed
