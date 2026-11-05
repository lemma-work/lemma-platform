from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.pod_account_share_as_request import PodAccountShareAsRequest
from ...models.pod_connector_response import PodConnectorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    pod_account_id: UUID,
    *,
    body: PodAccountShareAsRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/connector-accounts/{pod_account_id}/share-as".format(
            pod_id=quote(str(pod_id), safe=""),
            pod_account_id=quote(str(pod_account_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PodConnectorResponse | None:
    if response.status_code == 200:
        response_200 = PodConnectorResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PodConnectorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    pod_account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodAccountShareAsRequest,
) -> Response[ErrorResponse | PodConnectorResponse]:
    """Share Your Account As A Pod Connector

     Owner only, and the caller must also manage the pod's connectors: share this pod account with the
    pod (if it is still yours alone) and run the SHARED connector `slug` as it -- pointing the existing
    one, or declaring it. Checked in full before anything changes, and done in one transaction: a
    refusal leaves the account as it was.

    Args:
        pod_id (UUID):
        pod_account_id (UUID):
        body (PodAccountShareAsRequest): Share one of your pod accounts with the pod as a SHARED
            connector.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodConnectorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        pod_account_id=pod_account_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    pod_account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodAccountShareAsRequest,
) -> ErrorResponse | PodConnectorResponse | None:
    """Share Your Account As A Pod Connector

     Owner only, and the caller must also manage the pod's connectors: share this pod account with the
    pod (if it is still yours alone) and run the SHARED connector `slug` as it -- pointing the existing
    one, or declaring it. Checked in full before anything changes, and done in one transaction: a
    refusal leaves the account as it was.

    Args:
        pod_id (UUID):
        pod_account_id (UUID):
        body (PodAccountShareAsRequest): Share one of your pod accounts with the pod as a SHARED
            connector.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodConnectorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        pod_account_id=pod_account_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    pod_account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodAccountShareAsRequest,
) -> Response[ErrorResponse | PodConnectorResponse]:
    """Share Your Account As A Pod Connector

     Owner only, and the caller must also manage the pod's connectors: share this pod account with the
    pod (if it is still yours alone) and run the SHARED connector `slug` as it -- pointing the existing
    one, or declaring it. Checked in full before anything changes, and done in one transaction: a
    refusal leaves the account as it was.

    Args:
        pod_id (UUID):
        pod_account_id (UUID):
        body (PodAccountShareAsRequest): Share one of your pod accounts with the pod as a SHARED
            connector.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodConnectorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        pod_account_id=pod_account_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    pod_account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: PodAccountShareAsRequest,
) -> ErrorResponse | PodConnectorResponse | None:
    """Share Your Account As A Pod Connector

     Owner only, and the caller must also manage the pod's connectors: share this pod account with the
    pod (if it is still yours alone) and run the SHARED connector `slug` as it -- pointing the existing
    one, or declaring it. Checked in full before anything changes, and done in one transaction: a
    refusal leaves the account as it was.

    Args:
        pod_id (UUID):
        pod_account_id (UUID):
        body (PodAccountShareAsRequest): Share one of your pod accounts with the pod as a SHARED
            connector.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodConnectorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            pod_account_id=pod_account_id,
            client=client,
            body=body,
        )
    ).parsed
