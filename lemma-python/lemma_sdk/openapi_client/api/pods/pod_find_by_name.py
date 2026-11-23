from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.named_pod_response import NamedPodResponse
from ...types import UNSET, Response


def _get_kwargs(
    *,
    organization: str,
    pod: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["organization"] = organization

    params["pod"] = pod

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/by-name",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | NamedPodResponse | None:
    if response.status_code == 200:
        response_200 = NamedPodResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | NamedPodResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    organization: str,
    pod: str,
) -> Response[ErrorResponse | NamedPodResponse]:
    """Find a Pod by Name

     Which pod a web address spelled in names means (`/l/{organization}/{pod}/...`): `organization` is
    the organization's handle, `pod` the pod's name as one word of an address -- lower case, letters and
    digits, a hyphen for every other run. Answers only for a pod the caller could already see by name or
    walk into without asking: one they are in, any pod for the organization's owner, a pod open to
    everyone, and a pod open to the organization for anyone in it or free to join it. Every other name,
    an unknown handle, and a word two pods share are one 404. Open the pod, or ask to join it, by the
    id.

    Args:
        organization (str):
        pod (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NamedPodResponse]
    """

    kwargs = _get_kwargs(
        organization=organization,
        pod=pod,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    organization: str,
    pod: str,
) -> ErrorResponse | NamedPodResponse | None:
    """Find a Pod by Name

     Which pod a web address spelled in names means (`/l/{organization}/{pod}/...`): `organization` is
    the organization's handle, `pod` the pod's name as one word of an address -- lower case, letters and
    digits, a hyphen for every other run. Answers only for a pod the caller could already see by name or
    walk into without asking: one they are in, any pod for the organization's owner, a pod open to
    everyone, and a pod open to the organization for anyone in it or free to join it. Every other name,
    an unknown handle, and a word two pods share are one 404. Open the pod, or ask to join it, by the
    id.

    Args:
        organization (str):
        pod (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NamedPodResponse
    """

    return sync_detailed(
        client=client,
        organization=organization,
        pod=pod,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    organization: str,
    pod: str,
) -> Response[ErrorResponse | NamedPodResponse]:
    """Find a Pod by Name

     Which pod a web address spelled in names means (`/l/{organization}/{pod}/...`): `organization` is
    the organization's handle, `pod` the pod's name as one word of an address -- lower case, letters and
    digits, a hyphen for every other run. Answers only for a pod the caller could already see by name or
    walk into without asking: one they are in, any pod for the organization's owner, a pod open to
    everyone, and a pod open to the organization for anyone in it or free to join it. Every other name,
    an unknown handle, and a word two pods share are one 404. Open the pod, or ask to join it, by the
    id.

    Args:
        organization (str):
        pod (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NamedPodResponse]
    """

    kwargs = _get_kwargs(
        organization=organization,
        pod=pod,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    organization: str,
    pod: str,
) -> ErrorResponse | NamedPodResponse | None:
    """Find a Pod by Name

     Which pod a web address spelled in names means (`/l/{organization}/{pod}/...`): `organization` is
    the organization's handle, `pod` the pod's name as one word of an address -- lower case, letters and
    digits, a hyphen for every other run. Answers only for a pod the caller could already see by name or
    walk into without asking: one they are in, any pod for the organization's owner, a pod open to
    everyone, and a pod open to the organization for anyone in it or free to join it. Every other name,
    an unknown handle, and a word two pods share are one 404. Open the pod, or ask to join it, by the
    id.

    Args:
        organization (str):
        pod (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NamedPodResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            organization=organization,
            pod=pod,
        )
    ).parsed
