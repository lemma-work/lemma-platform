from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.plugin_install_request import PluginInstallRequest
from ...models.plugin_install_response import PluginInstallResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: PluginInstallRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/bundle/installs".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PluginInstallResponse | None:
    if response.status_code == 202:
        response_202 = PluginInstallResponse.from_dict(response.json())

        return response_202

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | PluginInstallResponse]:
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
    body: PluginInstallRequest,
) -> Response[ErrorResponse | PluginInstallResponse]:
    """Install Plugins

     Install several plugins -- bundles in folders of GitHub repositories -- into this pod, in the order
    named. Returns 202 with an install_id at once; the server fetches, plans and applies each with its
    install page's defaults, and keeps going when nobody polls. A plugin whose page would ask a question
    stops before anything is applied (NEEDS_ANSWER) and the next one goes ahead. Each plugin counts as
    one import against the daily import limit.

    Args:
        pod_id (UUID):
        body (PluginInstallRequest): Body for installing several plugins into one Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PluginInstallResponse]
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
    body: PluginInstallRequest,
) -> ErrorResponse | PluginInstallResponse | None:
    """Install Plugins

     Install several plugins -- bundles in folders of GitHub repositories -- into this pod, in the order
    named. Returns 202 with an install_id at once; the server fetches, plans and applies each with its
    install page's defaults, and keeps going when nobody polls. A plugin whose page would ask a question
    stops before anything is applied (NEEDS_ANSWER) and the next one goes ahead. Each plugin counts as
    one import against the daily import limit.

    Args:
        pod_id (UUID):
        body (PluginInstallRequest): Body for installing several plugins into one Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PluginInstallResponse
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
    body: PluginInstallRequest,
) -> Response[ErrorResponse | PluginInstallResponse]:
    """Install Plugins

     Install several plugins -- bundles in folders of GitHub repositories -- into this pod, in the order
    named. Returns 202 with an install_id at once; the server fetches, plans and applies each with its
    install page's defaults, and keeps going when nobody polls. A plugin whose page would ask a question
    stops before anything is applied (NEEDS_ANSWER) and the next one goes ahead. Each plugin counts as
    one import against the daily import limit.

    Args:
        pod_id (UUID):
        body (PluginInstallRequest): Body for installing several plugins into one Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PluginInstallResponse]
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
    body: PluginInstallRequest,
) -> ErrorResponse | PluginInstallResponse | None:
    """Install Plugins

     Install several plugins -- bundles in folders of GitHub repositories -- into this pod, in the order
    named. Returns 202 with an install_id at once; the server fetches, plans and applies each with its
    install page's defaults, and keeps going when nobody polls. A plugin whose page would ask a question
    stops before anything is applied (NEEDS_ANSWER) and the next one goes ahead. Each plugin counts as
    one import against the daily import limit.

    Args:
        pod_id (UUID):
        body (PluginInstallRequest): Body for installing several plugins into one Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PluginInstallResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed
