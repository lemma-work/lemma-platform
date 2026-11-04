from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.pod_connector_create_request import PodConnectorCreateRequest
from ...models.pod_connector_response import PodConnectorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: PodConnectorCreateRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/connectors".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PodConnectorResponse | None:
    if response.status_code == 201:
        response_201 = PodConnectorResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PodConnectorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodConnectorCreateRequest,
) -> Response[ErrorResponse | PodConnectorResponse]:
    """Declare A Pod Connector

     Declare a connector by an organization installation (`auth_config_id`) or by a catalog connector
    (`connector_id`): its organization default, or Lemma's own app for it installed now when the
    organization has none and the caller may install connectors. `slug` defaults to the installation's
    name; `mode` to PER_MEMBER.

    Args:
        pod_id (UUID):
        body (PodConnectorCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodConnectorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodConnectorCreateRequest,
) -> ErrorResponse | PodConnectorResponse | None:
    """Declare A Pod Connector

     Declare a connector by an organization installation (`auth_config_id`) or by a catalog connector
    (`connector_id`): its organization default, or Lemma's own app for it installed now when the
    organization has none and the caller may install connectors. `slug` defaults to the installation's
    name; `mode` to PER_MEMBER.

    Args:
        pod_id (UUID):
        body (PodConnectorCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodConnectorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodConnectorCreateRequest,
) -> Response[ErrorResponse | PodConnectorResponse]:
    """Declare A Pod Connector

     Declare a connector by an organization installation (`auth_config_id`) or by a catalog connector
    (`connector_id`): its organization default, or Lemma's own app for it installed now when the
    organization has none and the caller may install connectors. `slug` defaults to the installation's
    name; `mode` to PER_MEMBER.

    Args:
        pod_id (UUID):
        body (PodConnectorCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodConnectorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodConnectorCreateRequest,
) -> ErrorResponse | PodConnectorResponse | None:
    """Declare A Pod Connector

     Declare a connector by an organization installation (`auth_config_id`) or by a catalog connector
    (`connector_id`): its organization default, or Lemma's own app for it installed now when the
    organization has none and the caller may install connectors. `slug` defaults to the installation's
    name; `mode` to PER_MEMBER.

    Args:
        pod_id (UUID):
        body (PodConnectorCreateRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodConnectorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed
