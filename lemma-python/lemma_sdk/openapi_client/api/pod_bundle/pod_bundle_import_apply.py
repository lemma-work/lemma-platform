from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.apply_import_request import ApplyImportRequest
from ...models.error_response import ErrorResponse
from ...models.import_status_response import ImportStatusResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    import_id: UUID,
    *,
    body: ApplyImportRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/bundle/imports/{import_id}/apply".format(
            pod_id=quote(str(pod_id), safe=""),
            import_id=quote(str(import_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ImportStatusResponse | None:
    if response.status_code == 202:
        response_202 = ImportStatusResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ImportStatusResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    import_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ApplyImportRequest,
) -> Response[ErrorResponse | ImportStatusResponse]:
    """Apply Pod Import

     Apply a planned import. Requires confirm_destructive when the plan drops or alters columns, and
    resolved values for any required variables. Returns 202; poll the status endpoint for per-step
    progress.

    Args:
        pod_id (UUID):
        import_id (UUID):
        body (ApplyImportRequest): Body for applying a planned import.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ImportStatusResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        import_id=import_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    import_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ApplyImportRequest,
) -> ErrorResponse | ImportStatusResponse | None:
    """Apply Pod Import

     Apply a planned import. Requires confirm_destructive when the plan drops or alters columns, and
    resolved values for any required variables. Returns 202; poll the status endpoint for per-step
    progress.

    Args:
        pod_id (UUID):
        import_id (UUID):
        body (ApplyImportRequest): Body for applying a planned import.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ImportStatusResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        import_id=import_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    import_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ApplyImportRequest,
) -> Response[ErrorResponse | ImportStatusResponse]:
    """Apply Pod Import

     Apply a planned import. Requires confirm_destructive when the plan drops or alters columns, and
    resolved values for any required variables. Returns 202; poll the status endpoint for per-step
    progress.

    Args:
        pod_id (UUID):
        import_id (UUID):
        body (ApplyImportRequest): Body for applying a planned import.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ImportStatusResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        import_id=import_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    import_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ApplyImportRequest,
) -> ErrorResponse | ImportStatusResponse | None:
    """Apply Pod Import

     Apply a planned import. Requires confirm_destructive when the plan drops or alters columns, and
    resolved values for any required variables. Returns 202; poll the status endpoint for per-step
    progress.

    Args:
        pod_id (UUID):
        import_id (UUID):
        body (ApplyImportRequest): Body for applying a planned import.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ImportStatusResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            import_id=import_id,
            client=client,
            body=body,
        )
    ).parsed
